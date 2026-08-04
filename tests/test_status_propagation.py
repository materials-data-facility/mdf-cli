"""Tests for HTTP status propagation, success classification, and markup escaping.

Covers burndown items C-7 (preview against migrated records with no profile)
and C-9 (status-based error classification + Rich markup escaping).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from typer.testing import CliRunner

from mdf.cli.formatting import (
    handle_api_result,
    is_success,
    status_code_of,
)
from mdf.cli.main import app
from mdf.core.backend_client import ApiResult, BackendClient

runner = CliRunner()


class _DummyResponse:
    def __init__(self, payload: Any, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers: Dict[str, str] = {}

    def json(self) -> Any:
        if self._payload is _INVALID:
            raise ValueError("not json")
        return self._payload


_INVALID = object()


class _FixedHTTPClient:
    """httpx.Client stand-in that always returns one canned response."""

    def __init__(self, response: _DummyResponse):
        self._response = response

    def request(self, method, url, json=None, params=None, headers=None):
        return self._response

    def close(self) -> None:
        return None


def _client_returning(payload: Any, status_code: int = 200, text: str = "") -> BackendClient:
    client = BackendClient(base_url="https://api.example")
    client._client = _FixedHTTPClient(_DummyResponse(payload, status_code, text))
    return client


# --------------------------------------------------------------------------
# (a) status propagation
# --------------------------------------------------------------------------


def test_request_propagates_200_with_envelope():
    client = _client_returning({"success": True, "card": {"title": "T"}}, 200)
    result = client.get_card("src-1")
    assert result.status_code == 200
    assert status_code_of(result) == 200
    assert result["card"]["title"] == "T"
    assert is_success(result) is True


def test_request_propagates_200_without_envelope():
    client = _client_returning({"files": [{"filename": "a.csv"}]}, 200)
    result = client.dataset_preview("src-1")
    assert status_code_of(result) == 200
    # No {"success": ...} envelope, but a 2xx is still a success.
    assert is_success(result) is True


def test_request_propagates_404():
    client = _client_returning({"detail": "No profile found"}, 404)
    result = client.dataset_preview("src-1")
    assert status_code_of(result) == 404
    assert is_success(result) is False


def test_request_propagates_500():
    client = _client_returning({"detail": "boom"}, 500)
    result = client.dataset_preview("src-1")
    assert status_code_of(result) == 500
    assert is_success(result) is False


def test_request_propagates_status_on_invalid_json():
    client = _client_returning(_INVALID, 502, text="<html>bad gateway</html>")
    result = client.dataset_preview("src-1")
    assert status_code_of(result) == 502
    assert result["success"] is False


def test_request_prefers_inner_status_of_proxy_envelope():
    payload = {"statusCode": 404, "body": json.dumps({"detail": "No profile found"})}
    client = _client_returning(payload, 200)
    result = client.dataset_preview("src-1")
    assert status_code_of(result) == 404
    assert result["detail"] == "No profile found"


def test_api_result_is_a_plain_dict_for_json_output():
    result = ApiResult({"a": 1}, status_code=404)
    assert result == {"a": 1}
    # The status must never leak into --json output.
    assert json.loads(json.dumps(result)) == {"a": 1}


def test_connection_failure_has_no_status_code(monkeypatch):
    import httpx

    class _Failing:
        def request(self, *args, **kwargs):
            raise httpx.ConnectError("nope")

        def close(self):
            return None

    monkeypatch.setattr("time.sleep", lambda *_: None)
    client = BackendClient(base_url="https://api.example")
    client._client = _Failing()
    result = client.dataset_preview("src-1")
    assert status_code_of(result) is None
    assert is_success(result) is False


# --------------------------------------------------------------------------
# (b) the classification helper
# --------------------------------------------------------------------------


def test_is_success_uses_status_when_known():
    assert is_success(ApiResult({}, status_code=201)) is True
    assert is_success(ApiResult({"detail": "nope"}, status_code=404)) is False
    # An explicit failure envelope on a 2xx still loses.
    assert is_success(ApiResult({"success": False}, status_code=200)) is False


def test_is_success_falls_back_to_envelope_heuristic():
    assert is_success({"success": True}) is True
    assert is_success({"success": False}) is False
    # No envelope and no error marker: treat as success.
    assert is_success({"files": []}) is True
    assert is_success({"error": "kaboom"}) is False
    assert is_success({"detail": "Not found"}) is False


def test_status_code_of_accepts_reserved_key():
    assert status_code_of({"_status_code": 403}) == 403
    assert status_code_of({"_status_code": "500"}) == 500
    assert status_code_of({"_status_code": "not-a-number"}) is None
    assert status_code_of({}) is None


def test_handle_api_result_classifies_by_status(capsys):
    cases = {
        401: "Authentication required",
        403: "Permission denied",
        404: "Not found",
        429: "Rate limited",
        422: "Validation error",
        503: "Server error",
    }
    for status, expected in cases.items():
        assert handle_api_result(ApiResult({"detail": "x"}, status_code=status)) is False
        assert expected in capsys.readouterr().out


def test_handle_api_result_status_beats_misleading_message(capsys):
    # Message says "not found" but the server returned 403 — trust the status.
    result = ApiResult({"error": "record not found"}, status_code=403)
    assert handle_api_result(result) is False
    assert "Permission denied" in capsys.readouterr().out


def test_handle_api_result_falls_back_to_substrings_without_status(capsys):
    assert handle_api_result({"success": False, "error": "401 unauthorized"}) is False
    assert "Authentication required" in capsys.readouterr().out


# --------------------------------------------------------------------------
# (c) preview against a record with no profile (C-7)
# --------------------------------------------------------------------------


class _FakePreviewClient:
    def __init__(self, result):
        self._result = result

    def close(self) -> None:
        return None

    def search(self, query, search_type="datasets", limit=10):
        return {"results": []}

    def dataset_preview(self, source_id):
        return self._result

    def dataset_sample(self, source_id):
        return self._result


def _patch_preview(monkeypatch, result) -> None:
    monkeypatch.setattr(
        "mdf.cli.dataset_cmd.read_client",
        lambda **kwargs: _FakePreviewClient(result),
    )


def test_preview_404_degrades_gracefully(monkeypatch):
    _patch_preview(monkeypatch, ApiResult({"detail": "No profile found"}, status_code=404))
    result = runner.invoke(app, ["dataset", "preview", "migrated_v1"])
    assert result.exit_code == 0
    assert "No file profile exists for this dataset" in result.stdout
    assert "mdf show migrated_v1" in result.stdout
    assert "mdf clone migrated_v1" in result.stdout
    assert "No file information available" not in result.stdout


def test_preview_404_json_exits_zero(monkeypatch):
    _patch_preview(monkeypatch, ApiResult({"detail": "No profile found"}, status_code=404))
    result = runner.invoke(app, ["dataset", "preview", "migrated_v1", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["detail"] == "No profile found"


def test_preview_404_dataset_not_found_still_errors(monkeypatch):
    """A 404 for a nonexistent dataset (typo'd id) must NOT be treated as
    profile-missing: it keeps normal error handling and exits 1."""
    _patch_preview(monkeypatch, ApiResult({"detail": "Dataset not found"}, status_code=404))
    result = runner.invoke(app, ["dataset", "preview", "no-such-dataset"])
    assert result.exit_code == 1
    assert "No file profile exists" not in result.stdout


def test_preview_404_dataset_not_found_json_exits_one(monkeypatch):
    _patch_preview(monkeypatch, ApiResult({"detail": "Dataset not found"}, status_code=404))
    result = runner.invoke(app, ["dataset", "preview", "no-such-dataset", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["detail"] == "Dataset not found"


def test_preview_500_still_errors(monkeypatch):
    _patch_preview(monkeypatch, ApiResult({"detail": "boom"}, status_code=500))
    result = runner.invoke(app, ["dataset", "preview", "migrated_v1"])
    assert result.exit_code == 1
    assert "Preview failed" in result.stdout


def test_preview_200_without_envelope_renders_files(monkeypatch):
    _patch_preview(
        monkeypatch,
        ApiResult({"files": [{"filename": "data.csv", "size": 2048}]}, status_code=200),
    )
    result = runner.invoke(app, ["dataset", "preview", "migrated_v1"])
    assert result.exit_code == 0
    assert "data.csv" in result.stdout


# --------------------------------------------------------------------------
# (d) markup escaping regressions (C-9)
# --------------------------------------------------------------------------

MARKUP_TITLE = "[red]evil[/red]"


def test_versions_escapes_title_markup(monkeypatch):
    class FakeClient:
        def close(self):
            return None

        def search(self, query, search_type="datasets", limit=10):
            return {"results": []}

        def versions(self, source_id, limit=50, offset=0):
            return ApiResult(
                {"versions": [{"version": "1.0", "title": MARKUP_TITLE, "status": "published"}]},
                status_code=200,
            )

    monkeypatch.setattr("mdf.cli.dataset_cmd.read_client", lambda **kwargs: FakeClient())
    result = runner.invoke(app, ["dataset", "versions", "src-1"])
    assert result.exit_code == 0
    assert MARKUP_TITLE in result.stdout


def test_related_escapes_title_markup(monkeypatch):
    class FakeClient:
        def close(self):
            return None

        def search(self, query, search_type="datasets", limit=10):
            return {"results": []}

        def related_by_author(self, source_id, limit=20):
            return ApiResult(
                {"results": [{"source_id": "src-2", "title": MARKUP_TITLE}]},
                status_code=200,
            )

    monkeypatch.setattr("mdf.cli.main.read_client", lambda **kwargs: FakeClient())
    result = runner.invoke(app, ["related", "src-1"])
    assert result.exit_code == 0
    assert MARKUP_TITLE in result.stdout


def test_admin_pending_escapes_title_markup(monkeypatch):
    class FakeClient:
        def close(self):
            return None

        def curation_pending(self, limit=20, organization: Optional[str] = None):
            return ApiResult(
                {"submissions": [{"source_id": "src-1", "title": MARKUP_TITLE, "version": "1.0"}]},
                status_code=200,
            )

    monkeypatch.setattr(
        "mdf.cli.admin_cmd.BackendClient.authenticated", lambda **kwargs: FakeClient()
    )
    result = runner.invoke(app, ["admin", "pending"])
    assert result.exit_code == 0
    assert MARKUP_TITLE in result.stdout
