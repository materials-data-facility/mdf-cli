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
        service_instance="staging"
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
            service_instance="staging", interactive=False
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
            BackendClient.authenticated(service_instance="staging")
    assert _LoginNeededUserApp.login_calls == 0


def test_sdk_default_still_prompts_on_stderr(monkeypatch, capsys):
    """Foundry relies on the SDK prompting by default; the prompt must not touch stdout."""
    _no_cached_login(monkeypatch)
    BackendClient.authenticated(service_instance="staging")
    captured = capsys.readouterr()
    assert _LoginNeededUserApp.login_calls == 1
    assert captured.out == ""
    assert "Visit this URL" in captured.err
    assert "Please authenticate with Globus here:" in captured.err


# ---------------------------------------------------------------------------
# Review fixes: saved tokens are per API URL; tokens never go to other hosts
# ---------------------------------------------------------------------------

STAGING_URL = "https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging"


def _saved_token_home(monkeypatch, tmp_path, logged_in=False):
    from mdf.auth import globus as globus_auth
    from mdf.core import config as core_config

    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("MDF_CONNECT_TOKEN", "MDF_DEV_USER_ID", "MDF_CLIENT_ID", "MDF_CLIENT_SECRET", "MDF_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(globus_auth, "is_logged_in", lambda **kwargs: logged_in)
    core_config.save_token("saved-tok", STAGING_URL)
    return core_config


def test_saved_token_only_for_the_url_it_was_saved_for(monkeypatch, tmp_path):
    import pytest

    from mdf.auth.globus import interactive_login
    from mdf.core.exceptions import AuthRequired

    _saved_token_home(monkeypatch, tmp_path)
    monkeypatch.setattr("mdf.auth.globus.UserApp", _LoginNeededUserApp)
    monkeypatch.setattr("mdf.auth.globus.JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr("mdf.auth.globus.GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    assert BackendClient.authenticated(service_instance="staging")._token == "saved-tok"
    with interactive_login(False):
        # Another service, or an --api-url / MDF_API_URL host: never the saved token.
        with pytest.raises(AuthRequired):
            BackendClient.authenticated(service_instance="prod")
        with pytest.raises(AuthRequired):
            BackendClient.authenticated(base_url="https://evil.example", service_instance="staging")
        monkeypatch.setenv("MDF_API_URL", "https://evil.example")
        with pytest.raises(AuthRequired):
            BackendClient.authenticated(service_instance="staging")


def test_cached_globus_login_beats_a_saved_token(monkeypatch, tmp_path):
    from mdf.auth import globus as globus_auth

    _saved_token_home(monkeypatch, tmp_path, logged_in=True)
    monkeypatch.setattr(globus_auth, "UserApp", _FakeUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())
    client = BackendClient.authenticated(service_instance="staging")
    assert client._token == "tok-auth.globus.org"


def test_saved_token_file_is_atomic_owner_only_and_refuses_symlinks(monkeypatch, tmp_path):
    import os
    import stat

    import pytest

    core_config = _saved_token_home(monkeypatch, tmp_path)
    path = core_config.saved_token_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]

    path.unlink()
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    os.symlink(target, path)
    with pytest.raises(OSError, match="symlink"):
        core_config.save_token("t2", STAGING_URL)
    assert target.read_text() == "{}"


def test_data_token_only_goes_to_the_exact_mdf_host():
    from mdf.core.agent import _is_mdf_https_url

    assert _is_mdf_https_url("https://data.materialsdatafacility.org/mdf_open/x/a.csv")
    assert not _is_mdf_https_url("https://evilmaterialsdatafacility.org/a.csv")
    assert not _is_mdf_https_url("https://data.materialsdatafacility.org.evil.example/a.csv")
    # The stream download path that sent the API token to any *materialsdatafacility.org
    # host is gone with the stream client (CLI-D4).
    assert not [name for name in dir(BackendClient) if "stream" in name]


# ---------------------------------------------------------------------------
# Credential re-review (07f3eca): cached logins only go to trusted API hosts
# ---------------------------------------------------------------------------


def _cached_login(monkeypatch, tmp_path):
    from mdf.auth import globus as globus_auth

    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("MDF_CONNECT_TOKEN", "MDF_DEV_USER_ID", "MDF_CLIENT_ID", "MDF_CLIENT_SECRET",
                 "MDF_API_URL", "MDF_TRUST_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(globus_auth, "is_logged_in", lambda **kwargs: True)
    monkeypatch.setattr(globus_auth, "UserApp", _FakeUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())


def test_cached_login_is_not_sent_to_an_untrusted_api_url(monkeypatch, tmp_path):
    import pytest

    from mdf.core.exceptions import AuthRequired

    _cached_login(monkeypatch, tmp_path)
    for url in ("https://attacker.example", "https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com:8443/staging"):
        with pytest.raises(AuthRequired) as excinfo:
            BackendClient.authenticated(base_url=url, service_instance="staging")
        assert "MDF_TRUST_API_URL" in excinfo.value.hint
    monkeypatch.setenv("MDF_API_URL", "https://attacker.example")
    with pytest.raises(AuthRequired):
        BackendClient.authenticated(service_instance="staging")


def test_cached_login_goes_to_known_services_loopback_or_an_opted_in_host(monkeypatch, tmp_path):
    _cached_login(monkeypatch, tmp_path)
    for url in (
        STAGING_URL,
        STAGING_URL + "/",
        "http://localhost:8080",
        "http://127.0.0.1:9000",
        "http://[::1]:8080",
    ):
        client = BackendClient.authenticated(base_url=url, service_instance="staging")
        assert client._globus_data_token == f"tok-{NCSA_MDF_COLLECTION_UUID}", url
    monkeypatch.setenv("MDF_TRUST_API_URL", "1")
    assert BackendClient.authenticated(base_url="https://mine.example", service_instance="staging")._token
    # An explicit token is always the caller's choice, and carries nothing else.
    monkeypatch.delenv("MDF_TRUST_API_URL")
    client = BackendClient.authenticated(base_url="https://mine.example", token="t", service_instance="staging")
    assert client._token == "t" and client._globus_data_token is None


def test_credential_source_matches_the_real_resolution_order(monkeypatch, tmp_path):
    from mdf.core.backend_client import credential_source

    _cached_login(monkeypatch, tmp_path)
    from mdf.core import config as core_config

    core_config.save_token("saved", STAGING_URL)
    assert credential_source(STAGING_URL, service_instance="staging") == "cached_login"
    assert credential_source(STAGING_URL, service_instance="staging", dev_user_id="u") == "dev_user"
    monkeypatch.setattr("mdf.auth.globus.is_logged_in", lambda **kwargs: False)
    assert credential_source(STAGING_URL, service_instance="staging") == "saved_token"
    monkeypatch.setenv("MDF_CONNECT_TOKEN", "env")
    assert credential_source(STAGING_URL, service_instance="staging") == "env_token"


def test_saved_token_keys_normalize_scheme_host_port_and_slash_but_not_path(monkeypatch, tmp_path):
    from mdf.core import config as core_config

    monkeypatch.setenv("HOME", str(tmp_path))
    core_config.save_token("t1", "https://API.Example.org:443/Staging/")
    assert core_config.load_saved_token("https://api.example.org/Staging") == "t1"
    assert core_config.load_saved_token("https://api.example.org/staging") is None
    assert core_config.load_saved_token("https://api.example.org:8443/Staging") is None
    core_config.save_token("t2", "http://localhost:80")
    assert core_config.load_saved_token("http://LOCALHOST") == "t2"


def test_symlinked_token_file_is_never_read_and_logout_removes_the_link(monkeypatch, tmp_path):
    import json
    import os

    from mdf.core import config as core_config

    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"https://api.example.org": "planted"}))
    path = core_config.saved_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)
    assert core_config.load_saved_token("https://api.example.org") is None
    assert core_config.clear_saved_token() is True
    assert not path.exists() and not path.is_symlink()
    assert target.exists()  # the link's target is untouched


def test_data_token_host_check_requires_the_default_port():
    from mdf.core.agent import _is_mdf_https_url

    assert _is_mdf_https_url("https://data.materialsdatafacility.org:443/a.csv")
    assert not _is_mdf_https_url("https://data.materialsdatafacility.org:8443/a.csv")
    assert not _is_mdf_https_url("https://data.materialsdatafacility.org:notaport/a.csv")
