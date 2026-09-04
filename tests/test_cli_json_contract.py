"""The ``--json`` output contract: one envelope, one exit-code table.

Covers review items F-1 (``--json`` failures must not exit 0), F-2 (a single
error envelope), F-14 (``whoami --json`` derives ``success`` from auth state),
F-8 (``validate --json`` never prints Rich chrome to stdout), F-3 (``mdf list``
filters and pages server-side), F-4 (``search`` exposes the backend's facet /
sort / paging surface), F-16 (no retry storm on a prod connect timeout) and
F-19 (``dataset resubmit --yes``).

Everything here is offline: the backend seam is patched, never called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
from typer.testing import CliRunner

from mdf.cli.formatting import (
    EXIT_AUTH,
    EXIT_ERROR,
    EXIT_NETWORK,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE,
    error_envelope,
    exit_code_for,
)
from mdf.cli.main import app
from mdf.core.backend_client import ApiResult, BackendClient

runner = CliRunner()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


class FakeClient:
    """Backend stand-in: every method answers with one canned result."""

    def __init__(self, result: Any):
        self._result = result
        self.calls: List[tuple] = []

    def close(self) -> None:
        return None

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self._result

        return call

    # `search` is used by DOI resolution too; keep it from resolving anything.
    def search(self, query=None, search_type="all", limit=20, **kwargs):
        self.calls.append(
            ("search", (query,), {"search_type": search_type, "limit": limit, **kwargs})
        )
        if search_type == "datasets":
            return {"results": []}
        return self._result


def patch_read(monkeypatch, result, module: str = "mdf.cli.main") -> FakeClient:
    client = FakeClient(result)
    monkeypatch.setattr(f"{module}.read_client", lambda **kwargs: client)
    return client


def patch_auth(monkeypatch, result, module: str = "mdf.cli.main") -> FakeClient:
    client = FakeClient(result)
    monkeypatch.setattr(f"{module}.BackendClient.authenticated", lambda **kwargs: client)
    return client


NOT_FOUND = ApiResult({"detail": "Dataset not found"}, status_code=404)
UNAUTHORIZED = ApiResult({"detail": "Not authenticated"}, status_code=401)
FORBIDDEN = ApiResult({"detail": "nope"}, status_code=403)
SERVER_ERROR = ApiResult({"detail": "boom"}, status_code=503)
UNREACHABLE = {"success": False, "error": "Connection failed after 4 attempts: refused"}


# ---------------------------------------------------------------------------
# the exit-code table itself
# ---------------------------------------------------------------------------


class TestExitCodeTable:
    def test_codes_are_the_documented_five(self):
        assert (EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NOT_FOUND, EXIT_AUTH, EXIT_NETWORK) == (
            0, 1, 2, 3, 4, 5,
        )

    @pytest.mark.parametrize(
        "result,expected",
        [
            (ApiResult({"ok": True}, status_code=200), EXIT_OK),
            (ApiResult({"detail": "x"}, status_code=400), EXIT_USAGE),
            (ApiResult({"detail": "x"}, status_code=422), EXIT_USAGE),
            (NOT_FOUND, EXIT_NOT_FOUND),
            (UNAUTHORIZED, EXIT_AUTH),
            (FORBIDDEN, EXIT_AUTH),
            (ApiResult({"detail": "x"}, status_code=429), EXIT_NETWORK),
            (SERVER_ERROR, EXIT_NETWORK),
            (UNREACHABLE, EXIT_NETWORK),
            ({"success": False, "error": "kaboom"}, EXIT_ERROR),
        ],
    )
    def test_exit_code_for_maps_every_kind(self, result, expected):
        assert exit_code_for(result) == expected

    def test_a_2xx_error_envelope_is_still_classified_by_its_text(self):
        # Some endpoints answer 200 with {"success": false, "error": "..."}.
        result = ApiResult(
            {"success": False, "error": "No versions found for this source_id"},
            status_code=200,
        )
        assert exit_code_for(result) == EXIT_NOT_FOUND


class TestErrorEnvelope:
    def test_shape_is_success_false_plus_one_error_object(self):
        payload = error_envelope(NOT_FOUND)
        assert_error_envelope(payload, "not_found")
        assert payload["error"]["code"] == 404
        assert payload["error"]["hint"]

    def test_raw_server_text_survives_as_error_detail(self):
        payload = error_envelope(NOT_FOUND)
        assert payload["error"]["detail"] == "Dataset not found"

    def test_domain_keys_pass_through_at_the_top_level(self):
        payload = error_envelope(
            {"success": False, "error": "bad", "source_id": "x", "preflight": {"a": 1}}
        )
        assert payload["source_id"] == "x"
        assert payload["preflight"] == {"a": 1}
        # ...but the old top-level string `error` is replaced by the object.
        assert isinstance(payload["error"], dict)

    def test_code_is_null_when_no_http_status_arrived(self):
        payload = error_envelope(kind="validation", message="bad flag")
        assert payload["error"]["code"] is None
        assert payload["error"]["kind"] == "validation"


# ---------------------------------------------------------------------------
# F-1 / F-2 — every --json failure path
# ---------------------------------------------------------------------------


class TestJsonFailurePathsExitNonZero:
    def test_show_not_found(self, monkeypatch):
        patch_read(monkeypatch, NOT_FOUND)
        result = runner.invoke(app, ["show", "does-not-exist-xyz", "--json"])
        assert result.exit_code == EXIT_NOT_FOUND
        assert_error_envelope(envelope_of(result), "not_found")

    def test_show_success_gains_the_success_flag(self, monkeypatch):
        patch_read(monkeypatch, ApiResult({"card": {"source_id": "x"}}, status_code=200))
        result = runner.invoke(app, ["show", "x", "--json"])
        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert payload["success"] is True
        # Existing shape preserved: the card still lives under "card".
        assert payload["card"]["card"]["source_id"] == "x"

    def test_status_auth_failure(self, monkeypatch):
        patch_auth(monkeypatch, UNAUTHORIZED)
        monkeypatch.setattr(
            "mdf.cli.main._resolve_identifier_with_notice",
            lambda client, raw, **kwargs: raw,
        )
        result = runner.invoke(app, ["status", "src-1", "--json"])
        assert result.exit_code == EXIT_AUTH
        assert_error_envelope(envelope_of(result), "auth")

    def test_search_server_error(self, monkeypatch):
        patch_read(monkeypatch, SERVER_ERROR)
        result = runner.invoke(app, ["search", "perovskite", "--json"])
        assert result.exit_code == EXIT_NETWORK
        assert_error_envelope(envelope_of(result), "server")

    def test_list_forbidden(self, monkeypatch):
        patch_auth(monkeypatch, FORBIDDEN)
        result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == EXIT_AUTH
        assert_error_envelope(envelope_of(result), "forbidden")

    def test_related_not_found(self, monkeypatch):
        patch_read(monkeypatch, NOT_FOUND)
        result = runner.invoke(app, ["related", "does-not-exist-xyz", "--json"])
        assert result.exit_code == EXIT_NOT_FOUND
        assert_error_envelope(envelope_of(result), "not_found")

    def test_dataset_preview_not_found(self, monkeypatch):
        patch_read(monkeypatch, NOT_FOUND, module="mdf.cli.dataset_cmd")
        result = runner.invoke(app, ["dataset", "preview", "does-not-exist-xyz", "--json"])
        assert result.exit_code == EXIT_NOT_FOUND
        assert_error_envelope(envelope_of(result), "not_found")

    def test_dataset_preview_missing_profile_stays_an_empty_state(self, monkeypatch):
        # A migrated record with no generated profile is expected, not a failure.
        patch_read(
            monkeypatch,
            ApiResult({"detail": "No profile found"}, status_code=404),
            module="mdf.cli.dataset_cmd",
        )
        result = runner.invoke(app, ["dataset", "preview", "migrated_v1", "--json"])
        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert payload["success"] is True
        assert payload["detail"] == "No profile found"

    def test_dataset_versions_not_found(self, monkeypatch):
        patch_read(monkeypatch, NOT_FOUND, module="mdf.cli.dataset_cmd")
        result = runner.invoke(app, ["dataset", "versions", "does-not-exist-xyz", "--json"])
        assert result.exit_code == EXIT_NOT_FOUND
        assert_error_envelope(envelope_of(result), "not_found")

    def test_admin_pending_auth_failure(self, monkeypatch):
        patch_auth(monkeypatch, UNAUTHORIZED, module="mdf.cli.admin_cmd")
        result = runner.invoke(app, ["admin", "pending", "--json"])
        assert result.exit_code == EXIT_AUTH
        assert_error_envelope(envelope_of(result), "auth")

    def test_admin_pending_human_failure_also_exits_non_zero(self, monkeypatch):
        patch_auth(monkeypatch, UNAUTHORIZED, module="mdf.cli.admin_cmd")
        result = runner.invoke(app, ["admin", "pending"])
        assert result.exit_code == EXIT_AUTH

    def test_unknown_service_is_a_usage_error(self):
        result = runner.invoke(app, ["show", "x", "--json", "--service", "no-such-service"])
        assert result.exit_code == EXIT_USAGE
        assert_error_envelope(envelope_of(result), "validation")

    def test_related_rejects_an_unknown_relation_type(self):
        result = runner.invoke(app, ["related", "x", "--by", "sideways", "--json"])
        assert result.exit_code == EXIT_USAGE
        assert_error_envelope(envelope_of(result), "validation")


class TestJsonStdoutStaysPlain:
    def test_a_doi_resolution_notice_goes_to_stderr(self, monkeypatch):
        """The "Resolved DOI to source ID" chrome must not break `| jq`."""
        client = FakeClient(ApiResult({"card": {"source_id": "src-resolved"}}, status_code=200))
        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: client)
        monkeypatch.setattr(
            "mdf.cli.formatting.resolve_dataset_identifier",
            lambda raw_id, client: "src-resolved",
            raising=False,
        )
        monkeypatch.setattr(
            "mdf.cli.preflight.resolve_dataset_identifier",
            lambda raw_id, client: "src-resolved",
        )
        result = runner.invoke(app, ["show", "10.1234/example", "--json"])
        assert result.exit_code == EXIT_OK
        # stdout parses cleanly, i.e. the notice is not in it
        payload = envelope_of(result)
        assert payload["success"] is True


# ---------------------------------------------------------------------------
# F-14 — whoami / status --auth derive `success` from auth state
# ---------------------------------------------------------------------------


class TestWhoamiDerivesSuccess:
    def test_logged_out_is_success_false_and_exits_auth(self, monkeypatch):
        monkeypatch.setattr("mdf.auth.globus.is_logged_in", lambda **kwargs: False)
        monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)
        result = runner.invoke(app, ["whoami", "--json", "--service", "staging"])
        assert result.exit_code == EXIT_AUTH
        payload = envelope_of(result)
        assert_error_envelope(payload, "auth")
        # The identity fields survive so a caller can still report *why*.
        assert payload["status"] == "not authenticated"
        assert payload["service"] == "staging"

    def test_logged_in_is_success_true(self, monkeypatch):
        monkeypatch.setattr("mdf.auth.globus.is_logged_in", lambda **kwargs: True)
        result = runner.invoke(app, ["whoami", "--json", "--service", "staging"])
        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert payload["success"] is True
        assert payload["status"] == "authenticated"

    def test_status_auth_flag_agrees_with_whoami(self, monkeypatch):
        monkeypatch.setattr("mdf.auth.globus.is_logged_in", lambda **kwargs: False)
        monkeypatch.delenv("MDF_CONNECT_TOKEN", raising=False)
        monkeypatch.delenv("MDF_CLIENT_ID", raising=False)
        result = runner.invoke(app, ["status", "--auth", "--json", "--service", "staging"])
        assert result.exit_code == EXIT_AUTH
        assert envelope_of(result)["success"] is False


# ---------------------------------------------------------------------------
# F-8 — validate --json
# ---------------------------------------------------------------------------


class TestValidateJson:
    def test_empty_case_is_json_not_rich_chrome(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["validate", "--json"])
        assert result.exit_code == EXIT_USAGE
        payload = envelope_of(result)
        assert_error_envelope(payload, "validation")
        assert "No mdf.yaml found" in payload["error"]["message"]
        # The old shape's keys are still there for existing callers.
        assert payload["errors"] and payload["warnings"] == []

    def test_blocking_issues_exit_non_zero_with_the_report(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "d.csv").write_text("a,b\n1,2\n")
        result = runner.invoke(app, ["validate", "d.csv", "--json"])
        assert result.exit_code == EXIT_USAGE
        payload = envelope_of(result)
        assert payload["success"] is False
        assert payload["errors"]
        assert "preflight" in payload

    def test_passing_validation_is_success_true(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "d.csv").write_text("a,b\n1,2\n")
        monkeypatch.setattr(
            "mdf.cli.main.run_preflight",
            lambda *args, **kwargs: {"success": True, "issues": []},
        )
        result = runner.invoke(app, ["validate", "d.csv", "--json"])
        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert payload["success"] is True
        assert payload["errors"] == []


# ---------------------------------------------------------------------------
# F-3 — `mdf list` filters and pages server-side
# ---------------------------------------------------------------------------


class TestListPagesServerSide:
    def test_status_and_limit_go_to_the_backend(self, monkeypatch):
        seen: List[Dict[str, Any]] = []

        class PagingClient:
            def close(self):
                return None

            def submissions(self, organization=None, **kwargs):
                seen.append(kwargs)
                return {"success": True, "submissions": [], "next_key": None}

        monkeypatch.setattr(
            "mdf.cli.main.BackendClient.authenticated", lambda **kwargs: PagingClient()
        )
        result = runner.invoke(
            app, ["list", "--json", "--status", "published", "--limit", "7"]
        )
        assert result.exit_code == EXIT_OK
        assert seen[0]["status"] == "published"
        assert seen[0]["limit"] == 7
        assert seen[0]["include_counts"] is True

    def test_next_key_is_followed_until_limit_is_filled(self, monkeypatch):
        pages = [
            {
                "success": True,
                "submissions": [{"source_id": "a", "status": "published"}],
                "next_key": "cursor-1",
            },
            {
                "success": True,
                "submissions": [{"source_id": "b", "status": "published"}],
                "next_key": "cursor-2",
            },
            {
                "success": True,
                "submissions": [{"source_id": "c", "status": "published"}],
                "next_key": None,
            },
        ]
        seen: List[Optional[str]] = []

        class PagingClient:
            def close(self):
                return None

            def submissions(self, organization=None, **kwargs):
                seen.append(kwargs.get("start_key"))
                return pages[len(seen) - 1]

        monkeypatch.setattr(
            "mdf.cli.main.BackendClient.authenticated", lambda **kwargs: PagingClient()
        )
        result = runner.invoke(app, ["list", "--json", "--limit", "3"])
        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert [s["source_id"] for s in payload["submissions"]] == ["a", "b", "c"]
        # The cursor from each page was handed back on the next request.
        assert seen == [None, "cursor-1", "cursor-2"]
        assert payload["next_key"] is None

    def test_paging_stops_once_limit_is_reached(self, monkeypatch):
        calls = {"n": 0}

        class PagingClient:
            def close(self):
                return None

            def submissions(self, organization=None, **kwargs):
                calls["n"] += 1
                return {
                    "success": True,
                    "submissions": [
                        {"source_id": f"s{calls['n']}a", "status": "published"},
                        {"source_id": f"s{calls['n']}b", "status": "published"},
                    ],
                    "next_key": f"cursor-{calls['n']}",
                }

        monkeypatch.setattr(
            "mdf.cli.main.BackendClient.authenticated", lambda **kwargs: PagingClient()
        )
        result = runner.invoke(app, ["list", "--json", "--limit", "2"])
        assert result.exit_code == EXIT_OK
        payload = envelope_of(result)
        assert payload["count"] == 2
        assert calls["n"] == 1
        # The unconsumed cursor is echoed so a caller can resume.
        assert payload["next_key"] == "cursor-1"

    def test_client_side_status_filter_remains_a_backstop(self, monkeypatch):
        """A backend that ignores ?status= must not widen the result set."""

        class SloppyClient:
            def close(self):
                return None

            def submissions(self, organization=None, **kwargs):
                return {
                    "success": True,
                    "submissions": [
                        {"source_id": "keep", "status": "published"},
                        {"source_id": "drop", "status": "rejected"},
                    ],
                    "next_key": None,
                }

        monkeypatch.setattr(
            "mdf.cli.main.BackendClient.authenticated", lambda **kwargs: SloppyClient()
        )
        result = runner.invoke(app, ["list", "--json", "--status", "published"])
        payload = envelope_of(result)
        assert [s["source_id"] for s in payload["submissions"]] == ["keep"]

    def test_comma_separated_status_filter_accepts_several(self, monkeypatch):
        class MultiClient:
            def close(self):
                return None

            def submissions(self, organization=None, **kwargs):
                return {
                    "success": True,
                    "submissions": [
                        {"source_id": "p", "status": "published"},
                        {"source_id": "r", "status": "rejected"},
                        {"source_id": "w", "status": "withdrawn"},
                    ],
                    "next_key": None,
                }

        monkeypatch.setattr(
            "mdf.cli.main.BackendClient.authenticated", lambda **kwargs: MultiClient()
        )
        result = runner.invoke(app, ["list", "--json", "--status", "published,rejected"])
        payload = envelope_of(result)
        assert [s["source_id"] for s in payload["submissions"]] == ["p", "r"]


class TestBackendClientSubmissionsParams:
    def test_params_match_the_documented_contract(self):
        seen = {}

        class Recorder:
            def request(self, method, url, json=None, params=None, headers=None):
                seen["params"] = params

                class R:
                    status_code = 200
                    headers: Dict[str, str] = {}
                    text = ""

                    def json(self):
                        return {"success": True, "submissions": []}

                return R()

            def close(self):
                return None

        client = BackendClient(base_url="https://api.example")
        client._client = Recorder()
        client.submissions(
            status="published", limit=25, start_key="cursor", include_counts=True
        )
        assert seen["params"] == {
            "status": "published",
            "limit": 25,
            "start_key": "cursor",
            "include_counts": "true",
        }

    def test_no_params_stays_none(self):
        seen = {}

        class Recorder:
            def request(self, method, url, json=None, params=None, headers=None):
                seen["params"] = params

                class R:
                    status_code = 200
                    headers: Dict[str, str] = {}
                    text = ""

                    def json(self):
                        return {"success": True}

                return R()

            def close(self):
                return None

        client = BackendClient(base_url="https://api.example")
        client._client = Recorder()
        client.submissions()
        assert seen["params"] is None


# ---------------------------------------------------------------------------
# F-4 — search facet / sort / paging surface
# ---------------------------------------------------------------------------


def _recording_client():
    seen: Dict[str, Any] = {}

    class Recorder:
        def request(self, method, url, json=None, params=None, headers=None):
            seen["params"] = params
            seen["url"] = url

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
    return client, seen


class TestSearchParameterSurface:
    def test_every_documented_filter_is_passed_through(self):
        client, seen = _recording_client()
        client.search(
            "band gap",
            limit=5,
            offset=10,
            sort="newest",
            year=["2021"],
            organization=["MDF Open"],
            author=["Ward, Logan"],
            keyword=["DFT"],
            domain=["batteries"],
        )
        params = seen["params"]
        assert params["q"] == "band gap"
        assert params["limit"] == "5"
        assert params["offset"] == "10"
        assert params["sort"] == "newest"
        assert params["year"] == "2021"
        assert params["organization"] == "MDF Open"
        assert params["author"] == "Ward, Logan"
        assert params["keyword"] == "DFT"
        assert params["domain"] == "batteries"

    def test_repeated_values_stay_a_list_for_repeated_query_params(self):
        client, seen = _recording_client()
        client.search("alloy", author=["Ward, Logan", "Wolverton, Chris"])
        # httpx renders a list as ?author=A&author=B, which the backend ORs.
        assert seen["params"]["author"] == ["Ward, Logan", "Wolverton, Chris"]

    def test_omitting_the_query_is_a_browse_request(self):
        client, seen = _recording_client()
        client.search(None, limit=3)
        assert "q" not in seen["params"]
        assert seen["params"]["limit"] == "3"

    def test_empty_filters_are_not_sent(self):
        client, seen = _recording_client()
        client.search("x", year=[], author=None, keyword=[""])
        for name in BackendClient.SEARCH_FILTERS:
            assert name not in seen["params"]


class TestSearchCommandFlags:
    def test_cli_flags_reach_the_client(self, monkeypatch):
        client = FakeClient(
            ApiResult({"query": "band gap", "total": 0, "results": []}, status_code=200)
        )
        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: client)
        result = runner.invoke(
            app,
            [
                "search", "band gap", "--json",
                "--limit", "2",
                "--offset", "5",
                "--sort", "newest",
                "--year", "2021",
                "--organization", "MDF Open",
                "--author", "Ward, Logan",
                "--author", "Wolverton, Chris",
                "--keyword", "DFT",
                "--domain", "batteries",
            ],
        )
        assert result.exit_code == EXIT_OK
        kwargs = [c for c in client.calls if c[0] == "search"][0][2]
        assert kwargs["limit"] == 2
        assert kwargs["offset"] == 5
        assert kwargs["sort"] == "newest"
        assert kwargs["year"] == ["2021"]
        assert kwargs["author"] == ["Ward, Logan", "Wolverton, Chris"]
        assert kwargs["domain"] == ["batteries"]

    def test_query_is_optional_browse_mode(self, monkeypatch):
        client = FakeClient(
            ApiResult(
                {
                    "query": "",
                    "total": 1,
                    "results": [{"type": "dataset", "source_id": "a", "title": "T"}],
                },
                status_code=200,
            )
        )
        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: client)
        result = runner.invoke(app, ["search", "--json", "--limit", "1"])
        assert result.exit_code == EXIT_OK
        assert [c for c in client.calls if c[0] == "search"][0][1] == (None,)

    def test_bad_sort_value_is_a_usage_error(self):
        result = runner.invoke(app, ["search", "x", "--json", "--sort", "sideways"])
        assert result.exit_code == EXIT_USAGE
        assert_error_envelope(envelope_of(result), "validation")

    def test_semantic_with_filters_is_rejected_not_silently_ignored(self):
        result = runner.invoke(
            app, ["search", "x", "--json", "--semantic", "--year", "2021"]
        )
        assert result.exit_code == EXIT_USAGE
        payload = envelope_of(result)
        assert "facet filters" in payload["error"]["message"]

    def test_semantic_needs_a_query(self):
        result = runner.invoke(app, ["search", "--json", "--semantic"])
        assert result.exit_code == EXIT_USAGE
        assert_error_envelope(envelope_of(result), "validation")

    def test_facets_are_printed_in_human_mode(self, monkeypatch):
        client = FakeClient(
            ApiResult(
                {
                    "query": "band gap",
                    "total": 1,
                    "results": [
                        {"type": "dataset", "source_id": "a", "title": "T", "version": "1.0"}
                    ],
                    "facets": {
                        "Year": [{"value": "2021", "count": 8}],
                        "Organization": [{"value": "Foundry", "count": 5}],
                    },
                },
                status_code=200,
            )
        )
        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: client)
        result = runner.invoke(app, ["search", "band gap"])
        assert result.exit_code == EXIT_OK
        assert "Facets" in result.stdout
        # Each bucket is printed with the flag that selects it.
        assert "--year" in result.stdout
        assert "2021" in result.stdout
        assert "--organization" in result.stdout

    def test_brief_json_stays_a_bare_list(self, monkeypatch):
        client = FakeClient(
            ApiResult(
                {
                    "query": "x",
                    "total": 1,
                    "results": [{"title": "T", "authors": ["A"], "doi": "10.1/x"}],
                },
                status_code=200,
            )
        )
        monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: client)
        result = runner.invoke(app, ["search", "x", "--json", "--brief"])
        assert result.exit_code == EXIT_OK
        payload = json.loads(result.stdout)
        assert payload == [{"title": "T", "authors": ["A"], "doi": "10.1/x"}]


# ---------------------------------------------------------------------------
# F-16 — no retry storm on a prod connect timeout
# ---------------------------------------------------------------------------


class TestProdConnectTimeoutFastFails:
    @pytest.mark.parametrize("exc", [httpx.ConnectTimeout, httpx.ConnectError])
    def test_one_attempt_and_a_pointed_message(self, monkeypatch, exc):
        from mdf.core.backend_client import _V2_API_URLS

        attempts = {"n": 0}

        class Failing:
            def request(self, method, url, json=None, params=None, headers=None):
                attempts["n"] += 1
                raise exc("no route")

            def close(self):
                return None

        slept: List[float] = []
        monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

        client = BackendClient(base_url=_V2_API_URLS["prod"])
        client._client = Failing()
        result = client.health()

        assert attempts["n"] == 1, "prod must not be retried"
        assert slept == [], "and must not back off either"
        assert result["success"] is False
        assert "production service is not available yet" in result["error"]
        assert exit_code_for(result) == EXIT_NETWORK

    def test_other_services_still_retry(self, monkeypatch):
        attempts = {"n": 0}

        class Failing:
            def request(self, method, url, json=None, params=None, headers=None):
                attempts["n"] += 1
                raise httpx.ConnectTimeout("slow")

            def close(self):
                return None

        monkeypatch.setattr("time.sleep", lambda s: None)
        client = BackendClient(base_url="https://api-staging.example")
        client._client = Failing()
        result = client.health()
        assert attempts["n"] == BackendClient._MAX_RETRIES + 1
        assert result["success"] is False


# ---------------------------------------------------------------------------
# F-19 — dataset resubmit --yes
# ---------------------------------------------------------------------------


class TestResubmitConfirmation:
    def test_yes_flag_exists_and_skips_the_prompt(self, monkeypatch):
        client = FakeClient(ApiResult({"success": True}, status_code=200))
        monkeypatch.setattr(
            "mdf.cli.dataset_cmd.BackendClient.authenticated", lambda **kwargs: client
        )

        def refuse(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("--yes must not prompt")

        monkeypatch.setattr("typer.confirm", refuse)
        result = runner.invoke(app, ["dataset", "resubmit", "src-1", "--yes"])
        assert result.exit_code == EXIT_OK
        assert any(c[0] == "resubmit" for c in client.calls)

    def test_declining_the_prompt_aborts(self, monkeypatch):
        client = FakeClient(ApiResult({"success": True}, status_code=200))
        monkeypatch.setattr(
            "mdf.cli.dataset_cmd.BackendClient.authenticated", lambda **kwargs: client
        )
        monkeypatch.setattr("mdf.cli.dataset_cmd._interactive_stdin", lambda: True)
        monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
        result = runner.invoke(app, ["dataset", "resubmit", "src-1"])
        assert result.exit_code == EXIT_ERROR
        assert not any(c[0] == "resubmit" for c in client.calls)

    def test_json_without_yes_is_a_usage_error_not_a_silent_prompt(self, monkeypatch):
        client = FakeClient(ApiResult({"success": True}, status_code=200))
        monkeypatch.setattr(
            "mdf.cli.dataset_cmd.BackendClient.authenticated", lambda **kwargs: client
        )
        result = runner.invoke(app, ["dataset", "resubmit", "src-1", "--json"])
        assert result.exit_code == EXIT_USAGE
        assert_error_envelope(envelope_of(result), "validation")
        assert not any(c[0] == "resubmit" for c in client.calls)

    def test_help_documents_the_flag(self):
        result = runner.invoke(app, ["dataset", "resubmit", "--help"])
        assert result.exit_code == EXIT_OK
        assert "--yes" in result.stdout
