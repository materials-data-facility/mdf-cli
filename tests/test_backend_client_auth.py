from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any, Dict, Optional

from mdf.core.backend_client import BackendClient, _api_url_for_service
from mdf.auth.globus import NCSA_MDF_COLLECTION_UUID, get_scopes_for_service


class _DummyResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.text = ""
        self.status_code = status_code
        self.headers: Dict[str, str] = {}

    def json(self) -> Dict[str, Any]:
        return self._payload


class _RecordingHTTPClient:
    def __init__(self):
        self.last_headers: Optional[Dict[str, str]] = None

    def request(self, method: str, url: str, json: Any = None, params: Any = None, headers: Any = None):
        self.last_headers = headers or {}
        return _DummyResponse({"success": True})


def test_from_env_reads_auth(monkeypatch):
    monkeypatch.setenv("MDF_API_URL", "https://example.org")
    monkeypatch.setenv("MDF_CONNECT_TOKEN", "token-123")
    monkeypatch.setenv("MDF_DEV_USER_ID", "user-123")

    client = BackendClient.from_env()
    assert client.base_url == "https://example.org"
    assert client._token == "token-123"
    assert client._user_id == "user-123"


def test_request_prefers_token_header():
    client = BackendClient(base_url="https://example.org", token="abc", user_id="dev-user")
    recorder = _RecordingHTTPClient()
    client._client = recorder  # type: ignore[assignment]

    client.search("query")
    assert recorder.last_headers == {"Authorization": "Bearer abc"}


def test_request_uses_dev_user_header():
    client = BackendClient(base_url="https://example.org", user_id="dev-user")
    recorder = _RecordingHTTPClient()
    client._client = recorder  # type: ignore[assignment]

    client.search("query")
    assert recorder.last_headers == {"X-User-Id": "dev-user"}


def test_authenticated_uses_env_token(monkeypatch):
    monkeypatch.setenv("MDF_CONNECT_TOKEN", "env-token")
    monkeypatch.delenv("MDF_DEV_USER_ID", raising=False)

    client = BackendClient.authenticated(base_url="https://example.org", service_instance="dev")
    assert client._token == "env-token"
    assert client._user_id is None


def test_authenticated_local_without_credentials_no_oauth(monkeypatch):
    monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)
    monkeypatch.delenv("MDF_DEV_USER_ID", raising=False)
    monkeypatch.delenv("LOCAL_USER_ID", raising=False)

    client = BackendClient.authenticated(base_url="http://127.0.0.1:8080", service_instance="local")
    assert client._token is None
    assert client._user_id is None


def test_authenticated_uses_confidential_credentials(monkeypatch):
    monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)
    monkeypatch.delenv("MDF_DEV_USER_ID", raising=False)
    monkeypatch.setenv("MDF_CLIENT_ID", "client-id")
    monkeypatch.setenv("MDF_CLIENT_SECRET", "client-secret")

    _scope, resource_server = get_scopes_for_service("prod")
    captured: Dict[str, Any] = {}

    class _FakeConfidentialClient:
        def __init__(self, client_id: str, client_secret: str):
            captured["client_id"] = client_id
            captured["client_secret"] = client_secret

        def oauth2_client_credentials_tokens(self, requested_scopes: Optional[str] = None):
            captured["requested_scopes"] = requested_scopes
            return SimpleNamespace(
                by_resource_server={
                    resource_server: {"access_token": "service-token"},
                    NCSA_MDF_COLLECTION_UUID: {"access_token": "data-token"},
                }
            )

    fake_globus_sdk = SimpleNamespace(ConfidentialAppAuthClient=_FakeConfidentialClient)
    monkeypatch.setitem(sys.modules, "globus_sdk", fake_globus_sdk)

    client = BackendClient.authenticated(base_url="https://example.org", service_instance="prod")
    assert captured["client_id"] == "client-id"
    assert captured["client_secret"] == "client-secret"
    assert "requested_scopes" in captured
    assert client._token == "service-token"
    assert client._globus_data_token == "data-token"
    assert client._user_id is None


def test_api_url_for_service_respects_env_override(monkeypatch):
    monkeypatch.setenv("MDF_API_URL", "https://override.example")
    assert _api_url_for_service("prod") == "https://override.example"


# ---------------------------------------------------------------------------
# Groups token
#
# The server derives group memberships — and therefore submitter and
# curator-by-group rights — only from the Groups token we send in
# X-Groups-Token. It cannot fetch one itself: its dependent-token exchange is
# performed by a confidential client that is a different Globus app from the MDF
# Connect resource server, so Globus rejects that grant. So requesting the
# Groups scope at login, and forwarding the token, is load-bearing.
# ---------------------------------------------------------------------------


class _FakeUserApp:
    """Captures the scope requirements a UserApp is built with."""

    instances: list = []

    def __init__(self, app_name, client_id=None, config=None):
        self.scope_reqs: Dict[str, Any] = {}
        _FakeUserApp.instances.append(self)

    def add_scope_requirements(self, reqs):
        self.scope_reqs.update(reqs)

    def login_required(self):
        return False

    def login(self):  # pragma: no cover - login_required() is False
        raise AssertionError("should not need an interactive login")

    def get_authorizer(self, resource_server):
        return SimpleNamespace(
            get_authorization_header=lambda: f"Bearer tok-{resource_server}"
        )


def test_is_logged_in_requires_the_groups_scope(monkeypatch, tmp_path):
    """A token store predating the Groups scope must not count as logged in.

    Otherwise the CLI authenticates fine and then silently 403s on submit and
    curation, because it has no Groups token to forward.
    """
    from mdf.auth import globus as globus_auth

    _FakeUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _FakeUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    assert globus_auth.is_logged_in(service_instance="staging", token_path=token_path) is True

    reqs = _FakeUserApp.instances[-1].scope_reqs
    assert reqs["groups.api.globus.org"] == [globus_auth.GROUPS_SCOPE]
    # The connect scope is still required alongside it, not replaced.
    scope, resource_server = globus_auth.get_scopes_for_service("staging")
    assert reqs[resource_server] == [scope]


def test_is_logged_in_false_without_a_token_file(tmp_path):
    from mdf.auth import globus as globus_auth

    assert (
        globus_auth.is_logged_in(
            service_instance="staging", token_path=tmp_path / "missing.json"
        )
        is False
    )


def test_interactive_login_requests_groups_and_sends_the_header(monkeypatch):
    """The interactive path must both ask for the Groups scope and forward it."""
    from mdf.auth import globus as globus_auth

    monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)
    monkeypatch.delenv("MDF_DEV_USER_ID", raising=False)
    monkeypatch.delenv("MDF_CLIENT_ID", raising=False)
    monkeypatch.delenv("MDF_CLIENT_SECRET", raising=False)

    _FakeUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _FakeUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    client = BackendClient.authenticated(
        base_url="https://example.org", service_instance="staging"
    )

    requested = _FakeUserApp.instances[-1].scope_reqs
    assert requested["groups.api.globus.org"] == [globus_auth.GROUPS_SCOPE]
    assert client._groups_token == "tok-groups.api.globus.org"

    recorder = _RecordingHTTPClient()
    client._client = recorder  # type: ignore[assignment]
    client.curation_pending()
    assert recorder.last_headers["X-Groups-Token"] == "tok-groups.api.globus.org"


# ---------------------------------------------------------------------------
# C2 — core never prompts unless allowed
# ---------------------------------------------------------------------------


class _LoginNeededUserApp(_FakeUserApp):
    login_calls = 0

    def login_required(self):
        return True

    def login(self):
        _LoginNeededUserApp.login_calls += 1
        print("Please authenticate with Globus here:")  # what globus_sdk prints


def _no_cached_login(monkeypatch):
    from mdf.auth import globus as globus_auth

    for name in ("MDF_CONNECT_TOKEN", "MDF_DEV_USER_ID", "MDF_CLIENT_ID", "MDF_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    _LoginNeededUserApp.login_calls = 0
    monkeypatch.setattr(globus_auth, "UserApp", _LoginNeededUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())


def test_authenticated_non_interactive_raises_auth_required(monkeypatch, capsys):
    import pytest

    from mdf.core.exceptions import AuthRequired

    _no_cached_login(monkeypatch)
    with pytest.raises(AuthRequired):
        BackendClient.authenticated(
            base_url="https://example.org", service_instance="staging", interactive=False
        )
    captured = capsys.readouterr()
    assert _LoginNeededUserApp.login_calls == 0
    assert captured.out == "" and captured.err == ""


def test_interactive_login_scope_narrows_the_default(monkeypatch):
    import pytest

    from mdf.auth.globus import interactive_login
    from mdf.core.exceptions import AuthRequired

    _no_cached_login(monkeypatch)
    with interactive_login(False):
        with pytest.raises(AuthRequired):
            BackendClient.authenticated(base_url="https://example.org", service_instance="staging")
    assert _LoginNeededUserApp.login_calls == 0


def test_sdk_default_still_prompts_on_stderr(monkeypatch, capsys):
    """Foundry relies on the SDK prompting by default; the prompt must not touch stdout."""
    _no_cached_login(monkeypatch)
    BackendClient.authenticated(base_url="https://example.org", service_instance="staging")
    captured = capsys.readouterr()
    assert _LoginNeededUserApp.login_calls == 1
    assert captured.out == ""
    assert "Visit this URL" in captured.err
    assert "Please authenticate with Globus here:" in captured.err
