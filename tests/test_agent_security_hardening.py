"""Cross-family review fixes (wave 2, lane C-fix).

R2-1  data token must never reach a non-MDF host, and http:// is refused
R2-6  a FAILED anonymous clone plan must escalate to authenticated
R2-7  a successful authenticated retry must replace the failed anonymous result
R2-8  publish raises RuntimeError (CLI catches it) with distinct messages
R2-9  a partial multi-source transfer is not a success
R2-12 https destinations mirror the URL and never overwrite
R2-13 redirects are followed, but Authorization is not forwarded off-host
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

from mdf.core import submission as submission_mod
from mdf.core.agent import (
    MDFAgent,
    _download_https_file,
    _https_dest_for_url,
    _is_mdf_https_url,
    _resolve_globus_files,
)
from mdf.core.backend_client import ApiResult


MDF_BASE = "https://data.materialsdatafacility.org"


# ---------------------------------------------------------------------------
# httpx plumbing: a real client over a MockTransport so redirect/header
# handling is httpx's own, not a stub's.
# ---------------------------------------------------------------------------


def _patch_httpx_client(monkeypatch, handler) -> Dict[str, Any]:
    """Route every httpx.Client through a MockTransport; record constructor kwargs."""
    real_client_cls = httpx.Client
    recorded: Dict[str, Any] = {}

    def factory(**kwargs):
        recorded.update(kwargs)
        kwargs.pop("verify", None)
        return real_client_cls(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return recorded


def _seen_auth_headers(monkeypatch, url: str, dest: Path, token: str) -> List[Any]:
    """Download `url` and return the Authorization header seen per request."""
    seen: List[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, content=b"payload")

    _patch_httpx_client(monkeypatch, handler)
    _download_https_file(url, dest, token)
    return seen


# ---------------------------------------------------------------------------
# R2-1 — the data token is scoped to the MDF HTTPS base
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"{MDF_BASE}/mdf_open/ds/file.csv", True),
        (f"{MDF_BASE}/", True),
        ("https://DATA.MATERIALSDATAFACILITY.ORG/x.csv", True),
        # Look-alike / attacker-controlled hosts.
        ("https://data.materialsdatafacility.org.evil.com/x.csv", False),
        ("https://evil.com/data.materialsdatafacility.org/x.csv", False),
        ("https://example.com/x.csv", False),
        # Cleartext is never trusted, even on the right host.
        ("http://data.materialsdatafacility.org/x.csv", False),
        ("", False),
        ("not a url", False),
    ],
)
def test_is_mdf_https_url(url, expected):
    assert _is_mdf_https_url(url) is expected


def test_data_token_sent_to_mdf_base(monkeypatch, tmp_path):
    seen = _seen_auth_headers(
        monkeypatch, f"{MDF_BASE}/mdf_open/ds/file.csv", tmp_path / "f.csv", "secret-token"
    )
    assert seen == ["Bearer secret-token"]


def test_data_token_not_sent_to_foreign_host(monkeypatch, tmp_path):
    """A publisher-supplied data_source must not receive the cloner's token."""
    seen = _seen_auth_headers(
        monkeypatch, "https://evil.example.com/harvest.csv", tmp_path / "f.csv", "secret-token"
    )
    assert seen == [None]


def test_http_url_refused_outright(monkeypatch, tmp_path):
    """R2-1: no request at all — and therefore no header — for http://."""
    requests: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"payload")

    _patch_httpx_client(monkeypatch, handler)

    with pytest.raises(RuntimeError, match="only https:// URLs are supported"):
        _download_https_file("http://data.materialsdatafacility.org/x.csv", tmp_path / "f.csv", "secret-token")
    assert requests == []


def test_resolve_globus_files_rejects_non_https_schemes():
    resolved = _resolve_globus_files(
        [
            "https://ok.example/a.csv",
            "http://cleartext.example/b.csv",
            "ftp://legacy.example/c.csv",
            "stream://disabled",
        ],
        "transfer-token",
    )
    assert resolved.https_sources == ["https://ok.example/a.csv"]
    assert resolved.unsupported == [
        "http://cleartext.example/b.csv",
        "ftp://legacy.example/c.csv",
    ]


def test_clone_surfaces_unsupported_scheme_error(monkeypatch, tmp_path):
    class _FakeAnonClient:
        _token = _user_id = _globus_data_token = _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult(
                {
                    "source_id": source_id,
                    "title": "Cleartext Dataset",
                    "data_sources": ["http://cleartext.example/b.csv"],
                },
                status_code=200,
            )

        def close(self):
            pass

    # The anonymous plan fails (nothing downloadable), so the clone escalates —
    # the authenticated attempt must report the same clear scheme error.
    monkeypatch.setattr(
        "mdf.core.agent.BackendClient.authenticated", lambda **kwargs: _FakeAnonClient()
    )
    agent = MDFAgent()
    monkeypatch.setattr(agent, "_make_anonymous_client", lambda **kwargs: _FakeAnonClient())

    result = agent.clone("mdf-cleartext", output_dir=str(tmp_path), method="https")
    assert result["success"] is False
    assert "http://cleartext.example/b.csv" in result["error"]
    assert "only https://" in result["error"]


# ---------------------------------------------------------------------------
# R2-13 — redirects are followed; Authorization is not forwarded off-host
# ---------------------------------------------------------------------------


def test_download_follows_redirects(monkeypatch, tmp_path):
    """Zenodo-style 302 → object store must succeed, not fail."""
    auth_by_host: List[tuple] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_by_host.append((request.url.host, request.headers.get("Authorization")))
        if request.url.host == "zenodo.org":
            return httpx.Response(302, headers={"Location": "https://s3.example.com/blob"})
        return httpx.Response(200, content=b"redirected-body")

    recorded = _patch_httpx_client(monkeypatch, handler)

    dest = tmp_path / "out.bin"
    _download_https_file("https://zenodo.org/record/1/files/x.bin", dest, "secret-token")

    assert recorded.get("follow_redirects") is True
    assert dest.read_bytes() == b"redirected-body"
    # No token to zenodo (foreign host) and none to the redirect target either.
    assert auth_by_host == [("zenodo.org", None), ("s3.example.com", None)]


def test_authorization_stripped_on_cross_host_redirect(monkeypatch, tmp_path):
    """The MDF base may redirect; the token must not follow off-origin."""
    auth_by_host: List[tuple] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_by_host.append((request.url.host, request.headers.get("Authorization")))
        if request.url.host == "data.materialsdatafacility.org":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example/blob"})
        return httpx.Response(200, content=b"body")

    _patch_httpx_client(monkeypatch, handler)

    _download_https_file(f"{MDF_BASE}/mdf_open/ds/x.bin", tmp_path / "out.bin", "secret-token")

    assert auth_by_host == [
        ("data.materialsdatafacility.org", "Bearer secret-token"),
        ("elsewhere.example", None),
    ]


# ---------------------------------------------------------------------------
# R2-12 — destinations mirror the URL and never overwrite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected_parts",
    [
        ("https://example.com/a/b/file.csv", ("example.com", "a", "b", "file.csv")),
        ("https://EXAMPLE.com/file.csv", ("example.com", "file.csv")),
        ("https://example.com/", ("example.com", "download")),
        # Traversal segments are dropped, not honoured.
        ("https://example.com/a/../../etc/passwd", ("example.com", "a", "etc", "passwd")),
        ("https://example.com/%2E%2E/%2E%2E/etc/passwd", ("example.com", "etc", "passwd")),
        # Separators and oddities are sanitized into a single segment.
        ("https://example.com/dir/na%20me.csv", ("example.com", "dir", "na_me.csv")),
    ],
)
def test_https_dest_for_url(tmp_path, url, expected_parts):
    dest = _https_dest_for_url(url, tmp_path)
    assert dest.relative_to(tmp_path).parts == expected_parts
    assert tmp_path.resolve() in dest.resolve().parents


def _https_clone_agent(monkeypatch, sources: List[str]):
    class _FakeAnonClient:
        _token = _user_id = _globus_data_token = _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult(
                {"source_id": source_id, "title": "HTTPS Dataset", "data_sources": sources},
                status_code=200,
            )

        def close(self):
            pass

    agent = MDFAgent()
    monkeypatch.setattr(agent, "_make_anonymous_client", lambda **kwargs: _FakeAnonClient())
    monkeypatch.setattr(
        "mdf.core.agent.BackendClient.authenticated",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("no auth expected")),
    )
    return agent


def test_same_basename_across_hosts_no_longer_collides(monkeypatch, tmp_path):
    """Two sources named data.csv used to overwrite each other (files_count 2, 1 file)."""
    monkeypatch.setattr(
        "mdf.core.agent._download_https_file",
        lambda url, dest, token, **kwargs: dest.write_text(url),
    )
    agent = _https_clone_agent(
        monkeypatch, ["https://a.example/set1/data.csv", "https://b.example/set2/data.csv"]
    )

    result = agent.clone("mdf-two-hosts", output_dir=str(tmp_path), method="https")

    assert result["success"] is True
    assert result["files_count"] == 2
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == ["a.example/set1/data.csv", "b.example/set2/data.csv"]


def test_duplicate_source_refuses_to_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "mdf.core.agent._download_https_file",
        lambda url, dest, token, **kwargs: dest.write_text("first"),
    )
    agent = _https_clone_agent(
        monkeypatch, ["https://a.example/set/data.csv", "https://a.example/set/data.csv"]
    )

    result = agent.clone("mdf-dupe", output_dir=str(tmp_path), method="https")

    assert result["success"] is False
    assert result["files_count"] == 1
    assert any("refusing to overwrite" in e for e in result["errors"])
    assert (tmp_path / "a.example" / "set" / "data.csv").read_text() == "first"


# ---------------------------------------------------------------------------
# R2-6 / R2-7 — anonymous-first must escalate, and the retry must win
# ---------------------------------------------------------------------------


class _AnonNotFound:
    _token = _user_id = _globus_data_token = _globus_transfer_token = None

    def __init__(self):
        self.closed = False

    def get_card(self, source_id, version=None):
        return ApiResult({"detail": "Not found"}, status_code=404)

    def close(self):
        self.closed = True


def test_private_dataset_escalates_after_anonymous_404(monkeypatch, tmp_path):
    """R2-6: a FAILED anonymous plan must not be accepted as the plan."""
    anon = _AnonNotFound()
    auth_calls: List[Dict[str, Any]] = []

    class _FakeAuthClient:
        _token = "auth-token"
        _user_id = None
        _globus_data_token = "data-token"
        _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult(
                {
                    "source_id": source_id,
                    "title": "Private Dataset",
                    "data_sources": ["https://private.example/secret.csv"],
                },
                status_code=200,
            )

        def close(self):
            pass

    def fake_authenticated(**kwargs):
        auth_calls.append(kwargs)
        return _FakeAuthClient()

    monkeypatch.setattr("mdf.core.agent.BackendClient.authenticated", fake_authenticated)
    monkeypatch.setattr(
        "mdf.core.agent._download_https_file",
        lambda url, dest, token, **kwargs: dest.write_text("private"),
    )

    agent = MDFAgent()
    monkeypatch.setattr(agent, "_make_anonymous_client", lambda **kwargs: anon)

    result = agent.clone("mdf-private", output_dir=str(tmp_path), method="https")

    assert len(auth_calls) == 1, "authenticated retry must happen"
    assert anon.closed is True
    assert result["success"] is True
    assert result["files_count"] == 1
    assert result["title"] == "Private Dataset"


def test_plan_clone_escalates_after_anonymous_failure(monkeypatch, tmp_path):
    anon = _AnonNotFound()

    class _FakeAuthClient:
        _token = "auth-token"
        _user_id = None
        _globus_data_token = "data-token"
        _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult(
                {
                    "source_id": source_id,
                    "title": "Private Dataset",
                    "data_sources": ["https://private.example/secret.csv"],
                },
                status_code=200,
            )

        def close(self):
            pass

    monkeypatch.setattr(
        "mdf.core.agent.BackendClient.authenticated", lambda **kwargs: _FakeAuthClient()
    )
    agent = MDFAgent()
    monkeypatch.setattr(agent, "_make_anonymous_client", lambda **kwargs: anon)

    plan = agent.plan_clone("mdf-private", output_dir=str(tmp_path), method="https")

    assert plan["success"] is True
    assert plan["title"] == "Private Dataset"


def test_authenticated_retry_replaces_failed_anonymous_result(monkeypatch, tmp_path):
    """R2-7: the old `if result is None` guard kept the anonymous 403 forever."""

    class _FakeAnonClient:
        _token = _user_id = _globus_data_token = _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult(
                {
                    "source_id": source_id,
                    "title": "Restricted Files",
                    "data_sources": ["https://files.example/restricted.csv"],
                },
                status_code=200,
            )

        def close(self):
            pass

    class _FakeAuthClient(_FakeAnonClient):
        _token = "auth-token"
        _globus_data_token = "data-token"

    monkeypatch.setattr(
        "mdf.core.agent.BackendClient.authenticated", lambda **kwargs: _FakeAuthClient()
    )

    def fake_download(url, dest, token, **kwargs):
        # Anonymous plans carry no data token; the server answers 403.
        if not token:
            raise RuntimeError("HTTP 403 Forbidden")
        dest.write_text("restricted")
        return dest

    monkeypatch.setattr("mdf.core.agent._download_https_file", fake_download)

    agent = MDFAgent()
    monkeypatch.setattr(agent, "_make_anonymous_client", lambda **kwargs: _FakeAnonClient())

    result = agent.clone("mdf-restricted-files", output_dir=str(tmp_path), method="https")

    assert result["success"] is True
    assert result["files_count"] == 1
    assert not result.get("errors")


# ---------------------------------------------------------------------------
# R2-8 — publish raises RuntimeError, with distinct messages
# ---------------------------------------------------------------------------


class _ClientNoDataToken:
    _globus_data_token = None
    _globus_transfer_token = None

    def auth_check(self):
        return {"is_submitter": True}

    def submit(self, payload):  # pragma: no cover - must never be reached
        raise AssertionError("must not submit")

    def close(self):
        pass


def _agent_with_sources(monkeypatch, sources: List[str]) -> MDFAgent:
    agent = MDFAgent()
    monkeypatch.setattr(
        agent,
        "build_submission",
        lambda test=False, update=False: {
            "title": "T",
            "authors": [{"name": "Doe, Jane"}],
            "data_sources": list(sources),
        },
    )
    monkeypatch.setattr(
        "mdf.core.agent.BackendClient.authenticated", lambda **kwargs: _ClientNoDataToken()
    )
    return agent


def test_publish_local_without_token_raises_runtime_error(monkeypatch, tmp_path):
    """RuntimeError, because every CLI publish call site catches RuntimeError."""
    agent = _agent_with_sources(monkeypatch, [str(tmp_path / "data.csv")])
    with pytest.raises(RuntimeError, match=r"^Local files require a Globus data token"):
        agent.publish(dry_run=False, token="tok")


def test_publish_http_source_raises_unsupported_scheme(monkeypatch):
    """http:// used to be mangled into a local path and misreported."""
    agent = _agent_with_sources(monkeypatch, ["http://host.example/x.csv"])
    with pytest.raises(RuntimeError) as excinfo:
        agent.publish(dry_run=False, token="tok")

    message = str(excinfo.value)
    assert message.startswith("Unsupported data source scheme(s): http://host.example/x.csv")
    assert "use https://" in message
    assert "Globus data token" not in message


def test_publish_unsupported_scheme_message_differs_from_token_message(monkeypatch, tmp_path):
    agent_local = _agent_with_sources(monkeypatch, [str(tmp_path / "a.csv")])
    with pytest.raises(RuntimeError) as local_exc:
        agent_local.publish(dry_run=False, token="tok")

    agent_scheme = _agent_with_sources(monkeypatch, ["s3://bucket/key"])
    with pytest.raises(RuntimeError) as scheme_exc:
        agent_scheme.publish(dry_run=False, token="tok")

    assert str(local_exc.value) != str(scheme_exc.value)


def test_resolve_data_sources_does_not_mangle_http_urls(tmp_path):
    resolved = submission_mod.resolve_data_sources(
        ["http://host.example/x.csv", "s3://bucket/key", "local.csv"], tmp_path
    )
    assert resolved[0] == "http://host.example/x.csv"
    assert resolved[1] == "s3://bucket/key"
    assert resolved[2] == str((tmp_path / "local.csv").resolve())
    assert submission_mod.unsupported_data_sources(resolved) == [
        "http://host.example/x.csv",
        "s3://bucket/key",
    ]


# ---------------------------------------------------------------------------
# R2-9 — partial multi-source transfer: honest result + honest rendering
# ---------------------------------------------------------------------------


def _transfer_result(**overrides) -> Dict[str, Any]:
    result = {
        "success": False,
        "method": "transfer",
        "partial": True,
        "task_id": "task-1",
        "task_ids": ["task-1", "task-2"],
        "monitor_url": "https://app.globus.org/activity/task-1",
        "path": "/tmp/out",
        "source_id": "mdf-ds",
        "title": "Two Endpoint Dataset",
        "errors": ["globus://ep-3/x/: Endpoint ep-3 unavailable"],
        "failed_count": 1,
        "files_count": 0,
    }
    result.update(overrides)
    return result


def test_render_partial_transfer_shows_errors_and_all_task_ids(capsys):
    import typer

    from mdf.cli.commands import clone as cli_main

    with pytest.raises(typer.Exit) as excinfo:
        cli_main._render_clone_result(_transfer_result())
    assert excinfo.value.exit_code == 1

    out = capsys.readouterr().out
    assert "task-1" in out
    assert "task-2" in out
    assert "ep-3 unavailable" in out
    assert "partially" in out.lower()
    assert "Transfer queued!" not in out


def test_render_successful_transfer_shows_every_task_id(capsys):
    from mdf.cli.commands import clone as cli_main

    cli_main._render_clone_result(
        _transfer_result(success=True, partial=False, errors=None, failed_count=0)
    )
    out = capsys.readouterr().out
    assert "Transfer queued!" in out
    assert "task-1" in out
    assert "task-2" in out


def test_render_success_still_reports_partial_and_errors(capsys):
    """A success envelope carrying `partial`/`errors` must not hide them."""
    from mdf.cli.commands import clone as cli_main

    cli_main._render_clone_result(
        _transfer_result(success=True, partial=True, errors=["globus://ep-3/x/: boom"])
    )
    out = capsys.readouterr().out
    assert "partial" in out.lower()
    assert "boom" in out
