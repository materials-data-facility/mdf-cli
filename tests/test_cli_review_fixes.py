"""Regressions for the cross-family CLI review findings (MDF-01 … MDF-11).

One test per finding:

* MDF-01 — ``mdf list`` must not swallow a mid-stream page error and exit 0.
* MDF-02 — ``mdf list`` must not advance the server cursor past records the
  client-side filter never read.
* MDF-03 — ``mdf admin approve/reject/delete/rebuild-embeddings`` must not
  prompt on stdout under ``--json``.
* MDF-04/05/06 — human-mode ``dataset versions`` / ``status`` / ``related``
  must exit through the shared exit-code table on API failure.
* MDF-07 — an uncaught exception in ``publish`` / ``update`` / ``clone`` /
  ``import`` must become the JSON error envelope under ``--json``.
* MDF-08 — human-mode ``validate`` exits 2 (usage) on blocking issues.
* MDF-09 — ``search --brief`` treats ``"results": null`` as an empty list.
* MDF-10 — the top-level ``mdf resubmit`` alias takes ``--yes``.
* MDF-11 — ``_as_param_list`` accepts scalar numbers from the Python SDK.

Everything here is offline: the backend seam is patched, never called.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest
from typer.testing import CliRunner

from mdf.cli.formatting import (
    EXIT_NETWORK,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE,
)
from mdf.cli.main import app
from mdf.core.backend_client import ApiResult, BackendClient, _as_param_list

runner = CliRunner()


NOT_FOUND = ApiResult({"detail": "Dataset not found"}, status_code=404)
SERVER_ERROR = ApiResult({"detail": "boom"}, status_code=503)


def envelope_of(result) -> Dict[str, Any]:
    """Parse a command's stdout, asserting it is byte-plain JSON."""
    assert result.stdout, "expected a JSON payload on stdout"
    assert "\x1b[" not in result.stdout, "stdout carried ANSI styling"
    return json.loads(result.stdout)


def assert_error_envelope(payload: Dict[str, Any], kind: str) -> None:
    assert payload["success"] is False
    error = payload["error"]
    assert set(("kind", "code", "message", "hint")) <= set(error)
    assert error["kind"] == kind
    assert error["message"]


class RecordingClient:
    """Backend stand-in that answers each ``submissions()`` call from a script."""

    def __init__(self, pages: List[Any]):
        self._pages = pages
        self.start_keys: List[Optional[str]] = []

    def close(self) -> None:
        return None

    def submissions(self, organization=None, **kwargs):
        self.start_keys.append(kwargs.get("start_key"))
        return self._pages[len(self.start_keys) - 1]


class NeverCalledClient:
    """Any backend call is a test failure (used for the confirmation gates)."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def close(self) -> None:
        return None

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"backend call {name!r} must not happen")

        return call


def _patch_list_client(monkeypatch, pages: List[Any]) -> RecordingClient:
    client = RecordingClient(pages)
    monkeypatch.setattr(
        "mdf.core.backend_client.BackendClient.authenticated", lambda **kwargs: client
    )
    return client


def _page(records, next_key=None):
    return {"success": True, "submissions": records, "next_key": next_key}


# ---------------------------------------------------------------------------
# MDF-01 — a mid-stream page failure is a failure
# ---------------------------------------------------------------------------


class TestListSurfacesMidStreamPageErrors:
    def test_json_reports_the_error_partial_results_and_exits_non_zero(
        self, monkeypatch
    ):
        client = _patch_list_client(
            monkeypatch,
            [
                _page([{"source_id": "a", "status": "published"}], next_key="cursor-1"),
                SERVER_ERROR,
            ],
        )
        result = runner.invoke(app, ["list", "--json", "--limit", "5"])

        assert result.exit_code == EXIT_NETWORK
        payload = envelope_of(result)
        assert_error_envelope(payload, "server")
        # The page-1 records are not thrown away, but they are flagged partial.
        assert payload["partial"] is True
        assert payload["pages_read"] == 1
        assert payload["count"] == 1
        assert [s["source_id"] for s in payload["submissions"]] == ["a"]
        assert client.start_keys == [None, "cursor-1"]

    def test_human_mode_exits_non_zero_and_says_results_are_partial(self, monkeypatch):
        _patch_list_client(
            monkeypatch,
            [
                _page([{"source_id": "a", "status": "published"}], next_key="cursor-1"),
                SERVER_ERROR,
            ],
        )
        result = runner.invoke(app, ["list", "--limit", "5"])

        assert result.exit_code == EXIT_NETWORK
        assert "Listing failed" in result.stdout
        assert "Partial results" in result.stdout

    def test_a_first_page_failure_is_still_a_plain_failure(self, monkeypatch):
        _patch_list_client(monkeypatch, [NOT_FOUND])
        result = runner.invoke(app, ["list", "--json"])

        assert result.exit_code == EXIT_NOT_FOUND
        payload = envelope_of(result)
        assert_error_envelope(payload, "not_found")
        assert payload["partial"] is False
        assert payload["submissions"] == []


# ---------------------------------------------------------------------------
# MDF-02 — the cursor never moves past unread records
# ---------------------------------------------------------------------------


class TestListCursorNeverSkipsUnreadRecords:
    def test_stopping_mid_page_does_not_hand_back_the_next_page_cursor(
        self, monkeypatch
    ):
        client = _patch_list_client(
            monkeypatch,
            [
                _page(
                    [
                        {"source_id": "target-1", "status": "published"},
                        {"source_id": "target-2", "status": "published"},
                        {"source_id": "target-3", "status": "published"},
                    ],
                    next_key="cursor-1",
                )
            ],
        )
        result = runner.invoke(
            app, ["list", "--json", "--search", "target", "--limit", "1"]
        )

        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert [s["source_id"] for s in payload["submissions"]] == ["target-1"]
        # target-2/target-3 were never read: pointing at page 2 would skip them.
        assert payload["next_key"] != "cursor-1"
        assert payload["next_key"] is None
        assert payload["partial_page"] is True
        # And it did not fetch another page either.
        assert client.start_keys == [None]

    def test_the_resume_cursor_points_at_the_page_we_stopped_inside(self, monkeypatch):
        client = _patch_list_client(
            monkeypatch,
            [
                _page(
                    [{"source_id": "target-a", "status": "published"}],
                    next_key="cursor-1",
                ),
                _page(
                    [
                        {"source_id": "target-b", "status": "published"},
                        {"source_id": "target-c", "status": "published"},
                    ],
                    next_key="cursor-2",
                ),
            ],
        )
        result = runner.invoke(
            app, ["list", "--json", "--search", "target", "--limit", "2"]
        )

        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert [s["source_id"] for s in payload["submissions"]] == [
            "target-a",
            "target-b",
        ]
        # Page 2 was cut after its first record, so the resume cursor is the one
        # that re-reads page 2 — never "cursor-2", which would skip target-c.
        assert payload["next_key"] == "cursor-1"
        assert payload["partial_page"] is True
        assert client.start_keys == [None, "cursor-1"]

    def test_a_fully_scanned_page_still_advances_the_cursor(self, monkeypatch):
        _patch_list_client(
            monkeypatch,
            [
                _page(
                    [
                        {"source_id": "target-a", "status": "published"},
                        {"source_id": "target-b", "status": "published"},
                    ],
                    next_key="cursor-1",
                )
            ],
        )
        result = runner.invoke(
            app, ["list", "--json", "--search", "target", "--limit", "2"]
        )

        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert payload["next_key"] == "cursor-1"
        assert "partial_page" not in payload


# ---------------------------------------------------------------------------
# MDF-03 — admin mutations never prompt on stdout under --json
# ---------------------------------------------------------------------------


class TestAdminConfirmationUnderJson:
    @pytest.mark.parametrize(
        "argv",
        [
            ["admin", "approve", "src-1", "--json"],
            ["admin", "reject", "src-1", "--reason", "nope", "--json"],
            ["admin", "delete", "src-1", "--reason", "spam", "--json"],
            ["admin", "rebuild-embeddings", "--json"],
        ],
    )
    def test_json_without_yes_is_a_usage_envelope_and_never_prompts(
        self, monkeypatch, argv
    ):
        client = NeverCalledClient()
        monkeypatch.setattr(
            "mdf.core.backend_client.BackendClient.authenticated", lambda **kwargs: client
        )

        def refuse(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("--json must never prompt")

        monkeypatch.setattr("typer.confirm", refuse)

        result = runner.invoke(app, argv)
        assert result.exit_code == EXIT_USAGE
        payload = envelope_of(result)
        assert_error_envelope(payload, "validation")
        assert "--yes" in payload["error"]["hint"]
        assert client.calls == []

    @pytest.mark.parametrize(
        "argv",
        [
            ["admin", "approve", "src-1", "--yes", "--json"],
            ["admin", "reject", "src-1", "--reason", "nope", "--yes", "--json"],
        ],
    )
    def test_yes_skips_the_gate_and_reaches_the_backend(self, monkeypatch, argv):
        seen: List[str] = []

        class Client:
            def close(self):
                return None

            def __getattr__(self, name):
                def call(*args, **kwargs):
                    seen.append(name)
                    return ApiResult({"success": True}, status_code=200)

                return call

        monkeypatch.setattr(
            "mdf.core.backend_client.BackendClient.authenticated", lambda **kwargs: Client()
        )
        monkeypatch.setattr(
            "mdf.cli.preflight.resolve_dataset_identifier", lambda raw_id, client: raw_id
        )

        def refuse(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("--yes must not prompt")

        monkeypatch.setattr("typer.confirm", refuse)

        result = runner.invoke(app, argv)
        assert result.exit_code == EXIT_OK
        assert envelope_of(result)["success"] is True
        assert seen


# ---------------------------------------------------------------------------
# MDF-04/05/06 — human mode routes API failures through the exit-code table
# ---------------------------------------------------------------------------


class TestHumanModeFailuresExitNonZero:
    def test_dataset_versions(self, monkeypatch):
        monkeypatch.setattr(
            "mdf.cli.formatting.read_client", lambda **kwargs: _canned(NOT_FOUND)
        )
        result = runner.invoke(app, ["versions", "does-not-exist-xyz"])
        assert result.exit_code == EXIT_NOT_FOUND
        assert "Versions failed" in result.stdout

    def test_status(self, monkeypatch):
        monkeypatch.setattr(
            "mdf.core.backend_client.BackendClient.authenticated",
            lambda **kwargs: _canned(NOT_FOUND),
        )
        result = runner.invoke(app, ["status", "does-not-exist-xyz"])
        assert result.exit_code == EXIT_NOT_FOUND
        assert "Status failed" in result.stdout

    def test_related(self, monkeypatch):
        monkeypatch.setattr(
            "mdf.cli.formatting.read_client", lambda **kwargs: _canned(SERVER_ERROR)
        )
        result = runner.invoke(app, ["related", "src-1"])
        assert result.exit_code == EXIT_NETWORK
        assert "Related failed" in result.stdout


def _canned(result: Any):
    class Canned:
        def close(self):
            return None

        def __getattr__(self, name):
            def call(*args, **kwargs):
                # DOI resolution probes `search`; keep it from resolving.
                if name == "search":
                    return {"results": []}
                return result

            return call

    return Canned()


# ---------------------------------------------------------------------------
# MDF-07 — uncaught exceptions become the JSON envelope, not Rich on stdout
# ---------------------------------------------------------------------------


def _stub_preflight(monkeypatch):
    monkeypatch.setattr(
        "mdf.cli.commands.publish.run_preflight",
        lambda *args, **kwargs: {
            "success": True,
            "issues": [],
            "service": "staging",
            "target_url": "https://api.example",
            "source_summary": {"count": 1, "files": 1, "bytes": 8},
            "sources": [],
        },
    )


class TestExceptionBoundaryUnderJson:
    def test_publish_upload_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "data.csv").write_text("a,b\n1,2\n")
        _stub_preflight(monkeypatch)

        def boom(self, **kwargs):
            raise RuntimeError("upload aborted at 40%")

        monkeypatch.setattr("mdf.core.agent.MDFAgent.publish", boom)

        result = runner.invoke(
            app,
            [
                "publish",
                str(tmp_path / "data.csv"),
                "--title",
                "Test",
                "--author",
                "Jane",
                "--submit",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_NETWORK
        payload = envelope_of(result)
        assert_error_envelope(payload, "network")
        assert "upload aborted at 40%" in payload["error"]["message"]

    def test_publish_build_submission_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "data.csv").write_text("a,b\n1,2\n")
        _stub_preflight(monkeypatch)

        def boom(self, **kwargs):
            raise RuntimeError("cannot build payload: bad license")

        monkeypatch.setattr("mdf.core.agent.MDFAgent.build_submission", boom)

        result = runner.invoke(
            app,
            [
                "publish",
                str(tmp_path / "data.csv"),
                "--title",
                "Test",
                "--author",
                "Jane",
                "--submit",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_USAGE
        payload = envelope_of(result)
        assert_error_envelope(payload, "validation")
        assert "bad license" in payload["error"]["message"]

    def test_clone_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setattr(
            "mdf.core.agent.MDFAgent.plan_clone",
            lambda self, **kwargs: {
                "success": True,
                "resolved_source_id": "src-1",
                "method": "https",
                "files_count": 1,
                "total_bytes": 8,
                "path": str(tmp_path / "out"),
            },
        )

        def boom(self, **kwargs):
            raise RuntimeError("endpoint ep-3 unavailable")

        monkeypatch.setattr("mdf.core.agent.MDFAgent.clone", boom)

        result = runner.invoke(app, ["clone", "src-1", str(tmp_path / "out"), "--json"])
        assert result.exit_code == EXIT_NETWORK
        payload = envelope_of(result)
        assert_error_envelope(payload, "network")
        assert "ep-3 unavailable" in payload["error"]["message"]

    def test_import_metadata_fetch_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        class Adapter:
            @staticmethod
            def name():
                return "Zenodo"

            @staticmethod
            def fetch_metadata(identifier):
                raise RuntimeError("record 12345 is restricted")

        monkeypatch.setattr(
            "mdf.importers.registry.resolve_adapter", lambda identifier: Adapter()
        )

        result = runner.invoke(app, ["import", "zenodo:12345", "--json"])
        assert result.exit_code == EXIT_NETWORK
        payload = envelope_of(result)
        assert_error_envelope(payload, "network")
        assert "restricted" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# MDF-08 — validate exits 2 on blocking issues in human mode too
# ---------------------------------------------------------------------------


class TestCheckHumanExitCode:
    def test_blocking_issues_exit_usage(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "mdf.core.backend_client.BackendClient.health", lambda self: {"status": "ok"}
        )
        result = runner.invoke(app, ["check", "./does-not-exist.csv", "-t", "T", "-a", "A"])
        assert result.exit_code == EXIT_USAGE
        assert "does not exist" in result.stdout


# ---------------------------------------------------------------------------
# MDF-09 — `search --brief` survives a null result set
# ---------------------------------------------------------------------------


class TestSearchBriefWithNullResults:
    def test_null_results_is_an_empty_list(self, monkeypatch):
        class Client:
            def close(self):
                return None

            def search(self, *args, **kwargs):
                return ApiResult(
                    {"query": "x", "total": 0, "results": None}, status_code=200
                )

        monkeypatch.setattr("mdf.cli.formatting.read_client", lambda **kwargs: Client())
        result = runner.invoke(app, ["search", "x", "--json", "--brief"])
        assert result.exit_code == EXIT_OK
        assert envelope_of(result)["results"] == []


# ---------------------------------------------------------------------------
# MDF-10 — the top-level resubmit alias takes --yes
# ---------------------------------------------------------------------------


class TestResubmitYes:
    def test_help_documents_the_flag(self):
        result = runner.invoke(app, ["dataset", "resubmit", "--help"])
        assert result.exit_code == EXIT_OK
        assert "--yes" in result.stdout

    def test_yes_is_plumbed_through_and_skips_the_prompt(self, monkeypatch):
        seen: List[str] = []

        class Client:
            def close(self):
                return None

            def __getattr__(self, name):
                def call(*args, **kwargs):
                    seen.append(name)
                    return ApiResult({"success": True}, status_code=200)

                return call

        monkeypatch.setattr(
            "mdf.core.backend_client.BackendClient.authenticated", lambda **kwargs: Client()
        )
        monkeypatch.setattr("mdf.cli.dataset_cmd._interactive_stdin", lambda: True)

        def refuse(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("--yes must not prompt")

        monkeypatch.setattr("typer.confirm", refuse)

        result = runner.invoke(app, ["dataset", "resubmit", "src-1", "--yes"])
        assert result.exit_code == EXIT_OK
        assert "resubmit" in seen


# ---------------------------------------------------------------------------
# MDF-11 — scalar filter values from the Python SDK
# ---------------------------------------------------------------------------


class TestAsParamListAcceptsScalars:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, []),
            (2024, ["2024"]),
            (2024.0, ["2024.0"]),
            ("2024", ["2024"]),
            ([2024, 2025], ["2024", "2025"]),
            ((2024, ""), ["2024"]),
        ],
    )
    def test_scalars_and_sequences_normalize(self, value, expected):
        assert _as_param_list(value) == expected

    def test_search_with_an_integer_year_does_not_raise(self):
        seen: Dict[str, Any] = {}

        class Recorder:
            def request(self, method, url, json=None, params=None, headers=None):
                seen["params"] = params

                class R:
                    status_code = 200
                    headers: Dict[str, str] = {}
                    text = ""

                    def json(self):
                        return {"query": "", "total": 0, "results": []}

                return R()

            def close(self):
                return None

        client = BackendClient(base_url="https://api.example")
        client._client = Recorder()
        client.search("perovskite", year=2024)
        # One value stays a scalar param (`?year=2024`); lists become repeated
        # params. Either way the int no longer explodes in `list(2024)`.
        assert seen["params"]["year"] == "2024"
        client.search("perovskite", year=[2024, 2025])
        assert seen["params"]["year"] == ["2024", "2025"]
