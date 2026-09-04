from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from mdf.auth import globus as globus_auth


@pytest.fixture
def desktop_session(monkeypatch):
    """Make should_open_browser() say yes.

    R2-14: auto-opening a browser now requires a TTY on stdout, a GUI platform
    and no MDF_NO_BROWSER — under pytest stdout is captured, so tests that want
    the browser opened must say so explicitly.
    """
    monkeypatch.delenv("MDF_NO_BROWSER", raising=False)
    # Patch the module's `sys` reference rather than the real sys.stdout:
    # pytest swaps sys.stdout between the setup and call phases, so a fixture
    # patching it directly would not survive into the test body.
    monkeypatch.setattr(
        globus_auth,
        "sys",
        SimpleNamespace(platform="darwin", stdout=SimpleNamespace(isatty=lambda: True)),
    )


class _FakeUserApp:
    """Captures the scope requirements a UserApp is built with."""

    instances: list = []

    def __init__(self, app_name, client_id=None, config=None):
        self.app_name = app_name
        self.client_id = client_id
        self.config = config
        self.scope_reqs: Dict[str, Any] = {}
        self.login_called = False
        self._login_flow_manager = SimpleNamespace(
            print_authorize_url=MagicMock(),
        )
        _FakeUserApp.instances.append(self)

    def add_scope_requirements(self, reqs):
        self.scope_reqs.update(reqs)

    def login_required(self):
        return False

    def login(self):
        self.login_called = True

    def get_authorizer(self, resource_server):
        return SimpleNamespace(
            get_authorization_header=lambda: f"Bearer tok-{resource_server}"
        )


def test_login_required_for_missing_token_file(tmp_path):
    assert globus_auth.login_required_for(token_path=tmp_path / "nonexistent.json") is True


def test_login_required_for_default_scopes(monkeypatch, tmp_path):
    _FakeUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _FakeUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    assert globus_auth.login_required_for(token_path=token_path) is False

    reqs = _FakeUserApp.instances[-1].scope_reqs
    # Full set: Connect + Data + Transfer + Groups
    scope, resource_server = globus_auth.get_scopes_for_service(None)
    assert reqs[resource_server] == [scope]
    assert reqs[globus_auth.NCSA_MDF_COLLECTION_UUID] == [globus_auth.DATA_MDF_SCOPE]
    assert reqs["transfer.api.globus.org"] == [globus_auth.TRANSFER_SCOPE]
    assert reqs["groups.api.globus.org"] == [globus_auth.GROUPS_SCOPE]


def test_login_required_for_dev_service(monkeypatch, tmp_path):
    _FakeUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _FakeUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    assert globus_auth.login_required_for(service="dev", token_path=token_path) is False

    reqs = _FakeUserApp.instances[-1].scope_reqs
    assert reqs[globus_auth.MDF_CONNECT_DEV_RESOURCE_SERVER] == [globus_auth.MDF_CONNECT_DEV_SCOPE]
    assert reqs[globus_auth.NCSA_MDF_COLLECTION_UUID] == [globus_auth.DATA_MDF_SCOPE]
    assert reqs["transfer.api.globus.org"] == [globus_auth.TRANSFER_SCOPE]
    assert reqs["groups.api.globus.org"] == [globus_auth.GROUPS_SCOPE]


def test_login_required_for_explicit_scopes(monkeypatch, tmp_path):
    _FakeUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _FakeUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    assert globus_auth.login_required_for(
        scopes=[globus_auth.MDF_CONNECT_SCOPE],
        token_path=token_path,
    ) is False

    reqs = _FakeUserApp.instances[-1].scope_reqs
    assert list(reqs.keys()) == [globus_auth.MDF_CONNECT_RESOURCE_SERVER]
    assert reqs[globus_auth.MDF_CONNECT_RESOURCE_SERVER] == [globus_auth.MDF_CONNECT_SCOPE]


def test_login_required_for_returns_true_when_app_requires_login(monkeypatch, tmp_path):
    class _UnauthFakeUserApp(_FakeUserApp):
        def login_required(self):
            return True

    _UnauthFakeUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _UnauthFakeUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    assert globus_auth.login_required_for(token_path=token_path) is True


def test_is_logged_in_delegates_to_login_required_for(monkeypatch, tmp_path):
    calls = []

    def mock_login_required_for(scopes=None, *, service=None, client_id=None, token_path=globus_auth.DEFAULT_TOKEN_PATH):
        calls.append({"scopes": scopes, "service": service, "client_id": client_id, "token_path": token_path})
        return False

    monkeypatch.setattr(globus_auth, "login_required_for", mock_login_required_for)

    token_path = tmp_path / "tokens.json"
    result = globus_auth.is_logged_in(service_instance="staging", client_id="cid-123", token_path=token_path)
    assert result is True
    assert len(calls) == 1
    assert calls[0]["service"] == "staging"
    assert calls[0]["client_id"] == "cid-123"
    assert calls[0]["token_path"] == token_path

    # When login is required, is_logged_in returns False
    monkeypatch.setattr(globus_auth, "login_required_for", lambda *args, **kwargs: True)
    assert globus_auth.is_logged_in() is False


def test_honest_copy_printed_on_login(capsys, monkeypatch, tmp_path):
    class _LoginUserApp(_FakeUserApp):
        def login_required(self):
            return True

    _LoginUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _LoginUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())
    monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    globus_auth.get_authorizer(token_path=token_path, no_browser=True)
    out = capsys.readouterr().out
    assert "Visit this URL, sign in, and paste the code:" in out
    assert "Opening browser" not in out


def test_honest_copy_printed_on_get_authorizer_for_scopes(capsys, monkeypatch, tmp_path):
    class _LoginUserApp(_FakeUserApp):
        def login_required(self):
            return True

    _LoginUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _LoginUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    globus_auth.get_authorizer_for_scopes(
        scopes=[globus_auth.MDF_CONNECT_SCOPE],
        token_path=token_path,
        no_browser=True,
    )
    out = capsys.readouterr().out
    assert "Visit this URL, sign in, and paste the code:" in out
    assert "Opening browser" not in out


def test_browser_open_called_when_no_browser_false(monkeypatch, tmp_path, desktop_session):
    opened_urls = []
    monkeypatch.setattr(globus_auth.webbrowser, "open", lambda url: opened_urls.append(url))

    class _InteractiveUserApp(_FakeUserApp):
        def login_required(self):
            return True

        def login(self):
            # Simulates what CommandLineLoginFlowManager does:
            # prints the authorize URL before prompting
            self._login_flow_manager.print_authorize_url("https://auth.globus.org/v2/oauth2/authorize?test=1")

    _InteractiveUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _InteractiveUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())
    monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    globus_auth.get_authorizer(token_path=token_path, no_browser=False)
    assert len(opened_urls) == 1
    assert opened_urls[0] == "https://auth.globus.org/v2/oauth2/authorize?test=1"


def test_browser_open_not_called_when_no_browser_true(monkeypatch, tmp_path):
    opened_urls = []
    monkeypatch.setattr(globus_auth.webbrowser, "open", lambda url: opened_urls.append(url))

    class _InteractiveUserApp(_FakeUserApp):
        def login_required(self):
            return True

        def login(self):
            self._login_flow_manager.print_authorize_url("https://auth.globus.org/v2/oauth2/authorize?test=1")

    _InteractiveUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _InteractiveUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())
    monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    globus_auth.get_authorizer(token_path=token_path, no_browser=True)
    assert len(opened_urls) == 0


def test_browser_open_exception_handled_gracefully(monkeypatch, tmp_path, desktop_session):
    def faulty_open(url):
        raise OSError("No browser found")

    monkeypatch.setattr(globus_auth.webbrowser, "open", faulty_open)

    class _InteractiveUserApp(_FakeUserApp):
        def login_required(self):
            return True

        def login(self):
            self._login_flow_manager.print_authorize_url("https://auth.globus.org/v2/oauth2/authorize?test=1")

    _InteractiveUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _InteractiveUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())
    monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    # Should not raise despite browser open failure
    authorizer = globus_auth.get_authorizer(token_path=token_path, no_browser=False)
    assert authorizer is not None


def test_get_authorizer_for_scopes_browser_open_called_when_no_browser_false(monkeypatch, tmp_path, desktop_session):
    opened_urls = []
    monkeypatch.setattr(globus_auth.webbrowser, "open", lambda url: opened_urls.append(url))

    class _InteractiveUserApp(_FakeUserApp):
        def login_required(self):
            return True

        def login(self):
            self._login_flow_manager.print_authorize_url("https://auth.globus.org/v2/oauth2/authorize?test=scopes")

    _InteractiveUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _InteractiveUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    globus_auth.get_authorizer_for_scopes(
        scopes=[globus_auth.MDF_CONNECT_SCOPE],
        token_path=token_path,
        no_browser=False,
    )
    assert len(opened_urls) == 1
    assert opened_urls[0] == "https://auth.globus.org/v2/oauth2/authorize?test=scopes"


def test_get_authorizer_for_scopes_browser_open_not_called_when_no_browser_true(monkeypatch, tmp_path):
    opened_urls = []
    monkeypatch.setattr(globus_auth.webbrowser, "open", lambda url: opened_urls.append(url))

    class _InteractiveUserApp(_FakeUserApp):
        def login_required(self):
            return True

        def login(self):
            self._login_flow_manager.print_authorize_url("https://auth.globus.org/v2/oauth2/authorize?test=scopes")

    _InteractiveUserApp.instances = []
    monkeypatch.setattr(globus_auth, "UserApp", _InteractiveUserApp)
    monkeypatch.setattr(globus_auth, "JSONTokenStorage", lambda path: SimpleNamespace())
    monkeypatch.setattr(globus_auth, "GlobusAppConfig", lambda **kwargs: SimpleNamespace())

    token_path = tmp_path / "tokens.json"
    token_path.write_text("{}")

    globus_auth.get_authorizer_for_scopes(
        scopes=[globus_auth.MDF_CONNECT_SCOPE],
        token_path=token_path,
        no_browser=True,
    )
    assert len(opened_urls) == 0



# ---------------------------------------------------------------------------
# R2-14: browser-open decision + URL-before-browser ordering
# ---------------------------------------------------------------------------


def _fake_stdout(is_tty: bool) -> SimpleNamespace:
    return SimpleNamespace(isatty=lambda: is_tty)


@pytest.mark.parametrize(
    "no_browser,is_tty,platform,env,expected",
    [
        # Explicit opt-out always wins.
        (True, True, "darwin", {}, False),
        # Non-TTY stdout (pipe, CI log) → never open.
        (False, False, "darwin", {}, False),
        (False, False, "linux", {"DISPLAY": ":0"}, False),
        # GUI platforms need nothing else.
        (False, True, "darwin", {}, True),
        (False, True, "win32", {}, True),
        # Linux needs a display server.
        (False, True, "linux", {}, False),
        (False, True, "linux", {"DISPLAY": ":0"}, True),
        (False, True, "linux", {"WAYLAND_DISPLAY": "wayland-0"}, True),
        # MDF_NO_BROWSER overrides an otherwise-eligible session.
        (False, True, "darwin", {"MDF_NO_BROWSER": "1"}, False),
        (False, True, "darwin", {"MDF_NO_BROWSER": "true"}, False),
        (False, True, "darwin", {"MDF_NO_BROWSER": "YES"}, False),
        (False, True, "linux", {"DISPLAY": ":0", "MDF_NO_BROWSER": "on"}, False),
        # ...but a falsy value does not.
        (False, True, "darwin", {"MDF_NO_BROWSER": "0"}, True),
        (False, True, "darwin", {"MDF_NO_BROWSER": ""}, True),
    ],
)
def test_should_open_browser_decision(monkeypatch, no_browser, is_tty, platform, env, expected):
    for key in ("MDF_NO_BROWSER", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        globus_auth,
        "sys",
        SimpleNamespace(platform=platform, stdout=_fake_stdout(is_tty)),
    )

    assert globus_auth.should_open_browser(no_browser) is expected


def test_login_flow_prints_url_before_opening_browser(monkeypatch, desktop_session):
    """Headless users must see the URL even if the browser open blocks."""
    events: list[str] = []

    def blocking_open(url):
        events.append(f"open:{url}")
        return True

    monkeypatch.setattr(globus_auth.webbrowser, "open", blocking_open)

    app = _FakeUserApp("app")
    app._login_flow_manager = SimpleNamespace(
        print_authorize_url=lambda url: events.append(f"print:{url}")
    )
    url = "https://auth.globus.org/v2/oauth2/authorize?x=1"
    app.login = lambda: app._login_flow_manager.print_authorize_url(url)

    globus_auth._login_with_browser_fallback(app, no_browser=False)

    assert events == [f"print:{url}", f"open:{url}"]


def test_login_flow_prints_url_when_browser_suppressed(monkeypatch):
    """--no-browser still prints the URL — it is the only way to log in."""
    events: list[str] = []
    monkeypatch.setattr(
        globus_auth.webbrowser, "open", lambda url: events.append(f"open:{url}")
    )

    app = _FakeUserApp("app")
    app._login_flow_manager = SimpleNamespace(
        print_authorize_url=lambda url: events.append(f"print:{url}")
    )
    url = "https://auth.globus.org/v2/oauth2/authorize?x=2"
    app.login = lambda: app._login_flow_manager.print_authorize_url(url)

    globus_auth._login_with_browser_fallback(app, no_browser=True)

    assert events == [f"print:{url}"]
