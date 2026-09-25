from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from mdf.core.agent import MDFAgent, _resolve_globus_files, ResolvedFiles
from mdf.core.backend_client import BackendClient, ApiResult


class _DummyAuthorizer:
    def __init__(self, token: str):
        self.token = token


class _FakeTransferClient:
    def __init__(self, fail_for: Optional[set[str]] = None):
        self.fail_for = fail_for or set()
        self.submitted: List[Any] = []

    def get_submission_id(self) -> Dict[str, Any]:
        return {"value": "sub-uuid"}

    def submit_transfer(self, td: Any) -> Dict[str, Any]:
        self.submitted.append(td)
        for ep in self.fail_for:
            if td.get("source_endpoint") == ep:
                raise RuntimeError(f"Endpoint {ep} unavailable")
        return {"task_id": f"task-{len(self.submitted)}"}


def test_clone_via_transfer_accumulates_task_ids(monkeypatch, tmp_path):
    monkeypatch.setattr("mdf.core.agent._detect_gcp_endpoint", lambda: "local-gcp-uuid")
    fake_tc = _FakeTransferClient()
    import globus_sdk

    monkeypatch.setattr(globus_sdk, "AccessTokenAuthorizer", lambda t: _DummyAuthorizer(t))
    monkeypatch.setattr(globus_sdk, "TransferClient", lambda authorizer: fake_tc)

    agent = MDFAgent()
    sources = [
        "globus://endpoint-1/path/dir1/",
        "globus://endpoint-2/path/dir2/",
    ]
    card = {"source_id": "mdf-test-ds", "title": "Test Dataset"}

    result = agent._clone_via_transfer(sources, "fake-transfer-token", tmp_path, card)

    assert result["success"] is True
    assert result["partial"] is False
    assert result["task_ids"] == ["task-1", "task-2"]
    assert result["task_id"] == "task-1"
    assert "activity/task-1" in result["monitor_url"]
    assert len(fake_tc.submitted) == 2


def test_clone_via_transfer_partial_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("mdf.core.agent._detect_gcp_endpoint", lambda: "local-gcp-uuid")
    fake_tc = _FakeTransferClient(fail_for={"endpoint-2"})
    import globus_sdk

    monkeypatch.setattr(globus_sdk, "AccessTokenAuthorizer", lambda t: _DummyAuthorizer(t))
    monkeypatch.setattr(globus_sdk, "TransferClient", lambda authorizer: fake_tc)

    agent = MDFAgent()
    sources = [
        "globus://endpoint-1/path/dir1/",
        "globus://endpoint-2/path/dir2/",
    ]
    card = {"source_id": "mdf-test-ds", "title": "Test Dataset"}

    result = agent._clone_via_transfer(sources, "fake-transfer-token", tmp_path, card)

    # R2-9: a partial transfer must NOT report success — the caller has to see
    # the per-source failures instead of a bare "Transfer queued!".
    assert result["success"] is False
    assert result["partial"] is True
    assert result["task_ids"] == ["task-1"]
    assert result["task_id"] == "task-1"
    assert result["errors"] is not None
    assert any("endpoint-2" in str(e) for e in result["errors"])


def test_publish_local_files_requires_globus_data_token(monkeypatch, tmp_path):
    agent = MDFAgent()
    local_file = tmp_path / "data.csv"
    local_file.write_text("a,b,c")

    monkeypatch.setattr(
        agent,
        "build_submission",
        lambda test=False, update=False: {
            "title": "My Dataset",
            "authors": [{"name": "Doe, Jane"}],
            "data_sources": [str(local_file)],
        },
    )

    class _ClientNoDataToken:
        _globus_data_token = None
        _globus_transfer_token = None

        def auth_check(self):
            return {"is_submitter": True}

        def close(self):
            pass

        def submit(self, payload):
            return {"success": True, "source_id": "test-id"}

    monkeypatch.setattr(
        "mdf.core.agent.BackendClient.authenticated",
        lambda **kwargs: _ClientNoDataToken(),
    )

    with pytest.raises(RuntimeError, match=r"Local files require a Globus data token"):
        agent.publish(dry_run=False, token="tok-123")


def test_publish_remote_data_sources_allowed_without_data_token(monkeypatch):
    agent = MDFAgent()
    submitted_payload: Dict[str, Any] = {}

    monkeypatch.setattr(
        agent,
        "build_submission",
        lambda test=False, update=False: {
            "title": "Remote Dataset",
            "authors": [{"name": "Doe, Jane"}],
            "data_sources": ["globus://endpoint-uuid/dataset/path", "https://example.com/file.csv"],
        },
    )

    class _ClientNoDataToken:
        _globus_data_token = None
        _globus_transfer_token = None

        def auth_check(self):
            return {"is_submitter": True}

        def close(self):
            pass

        def submit(self, payload):
            submitted_payload.update(payload)
            return {"success": True, "source_id": "test-id"}

    monkeypatch.setattr(
        "mdf.core.agent.BackendClient.authenticated",
        lambda **kwargs: _ClientNoDataToken(),
    )

    result = agent.publish(dry_run=False, token="tok-123")
    assert result["success"] is True
    assert submitted_payload["data_sources"] == [
        "globus://endpoint-uuid/dataset/path",
        "https://example.com/file.csv",
    ]


def test_resolve_globus_files_separates_https_sources():
    ncsa_uuid = "82f1b5c6-6e9b-11e5-ba47-22000b92c6ec"
    data_sources = [
        f"globus://{ncsa_uuid}/mdf_open/ds1/file.csv",
        "https://example.com/ext_file1.csv",
        "http://example.org/ext_file2.json",
        "stream://disabled-stream",
    ]
    resolved = _resolve_globus_files(data_sources, "fake-transfer-token")
    assert isinstance(resolved, ResolvedFiles)
    assert len(resolved.files) == 1
    assert resolved.files[0] == (
        "https://data.materialsdatafacility.org/mdf_open/ds1/file.csv",
        "file.csv",
    )
    assert resolved.https_sources == ["https://example.com/ext_file1.csv"]
    # R2-1: cleartext http:// is rejected outright, never downloaded.
    assert resolved.unsupported == ["http://example.org/ext_file2.json"]


def test_resolve_globus_files_iterates_as_url_path_pairs():
    """foundry does `for url, rel in _resolve_globus_files(...)`, the 0.2.0 list shape."""
    ncsa_uuid = "82f1b5c6-6e9b-11e5-ba47-22000b92c6ec"
    resolved = _resolve_globus_files(
        [f"globus://{ncsa_uuid}/mdf_open/ds1/file.csv", "https://example.com/ext.csv"], ""
    )
    assert [(url, rel) for url, rel in resolved] == [
        ("https://data.materialsdatafacility.org/mdf_open/ds1/file.csv", "file.csv")
    ]
    assert len(resolved) == 1


def test_clone_https_sources_downloaded_directly(monkeypatch, tmp_path):
    downloaded_urls: List[tuple[str, Path]] = []

    def fake_download_https_file(url, dest_path, token, progress_callback=None):
        downloaded_urls.append((url, dest_path))
        dest_path.write_text("data")
        return dest_path

    monkeypatch.setattr("mdf.core.agent._download_https_file", fake_download_https_file)

    class _FakeAnonClient:
        _token = None
        _user_id = None
        _globus_data_token = None
        _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult({
                "source_id": source_id,
                "title": "Public HTTPS Dataset",
                "data_sources": [
                    "https://example.com/data/sample1.csv",
                    "https://example.com/sample2.json",
                ],
            }, status_code=200)

        def close(self):
            pass

    agent = MDFAgent()
    monkeypatch.setattr(agent, "_make_anonymous_client", lambda **kwargs: _FakeAnonClient())

    result = agent.clone("mdf-public-https", output_dir=str(tmp_path), method="https")

    assert result["success"] is True
    assert result["files_count"] == 2
    assert len(downloaded_urls) == 2
    assert downloaded_urls[0][0] == "https://example.com/data/sample1.csv"
    assert downloaded_urls[0][1] == tmp_path / "example.com" / "data" / "sample1.csv"
    assert downloaded_urls[1][0] == "https://example.com/sample2.json"
    assert downloaded_urls[1][1] == tmp_path / "example.com" / "sample2.json"


def test_clone_anonymous_first_when_public(monkeypatch, tmp_path):
    auth_called = False

    def fake_authenticated(**kwargs):
        nonlocal auth_called
        auth_called = True
        raise AssertionError("BackendClient.authenticated should not be called for public dataset!")

    monkeypatch.setattr("mdf.core.agent.BackendClient.authenticated", fake_authenticated)

    class _FakeAnonClient:
        _token = None
        _user_id = None
        _globus_data_token = None
        _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult({
                "source_id": source_id,
                "title": "Public Dataset",
                "data_sources": ["https://example.com/file.csv"],
            }, status_code=200)

        def close(self):
            pass

    monkeypatch.setattr("mdf.core.agent._download_https_file", lambda url, dest, token, **kwargs: dest.write_text("x"))

    agent = MDFAgent()
    monkeypatch.setattr(agent, "_make_anonymous_client", lambda **kwargs: _FakeAnonClient())

    result = agent.clone("mdf-public-ds", output_dir=str(tmp_path), method="https")

    assert result["success"] is True
    assert auth_called is False


def test_clone_escalates_to_authenticated_on_401_or_403(monkeypatch, tmp_path):
    anon_closed = False
    auth_called = False

    class _FakeAnonClient:
        _token = None
        _user_id = None
        _globus_data_token = None
        _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult({"detail": "Not authorized"}, status_code=401)

        def close(self):
            nonlocal anon_closed
            anon_closed = True

    class _FakeAuthClient:
        _token = "auth-token"
        _user_id = None
        _globus_data_token = "data-token"
        _globus_transfer_token = None

        def get_card(self, source_id, version=None):
            return ApiResult({
                "source_id": source_id,
                "title": "Restricted Dataset",
                "data_sources": ["https://example.com/restricted_file.csv"],
            }, status_code=200)

        def close(self):
            pass

    def fake_authenticated(**kwargs):
        nonlocal auth_called
        auth_called = True
        return _FakeAuthClient()

    monkeypatch.setattr("mdf.core.agent.BackendClient.authenticated", fake_authenticated)
    monkeypatch.setattr("mdf.core.agent._download_https_file", lambda url, dest, token, **kwargs: dest.write_text("secret"))

    agent = MDFAgent()
    monkeypatch.setattr(agent, "_make_anonymous_client", lambda **kwargs: _FakeAnonClient())

    result = agent.clone("mdf-restricted-ds", output_dir=str(tmp_path), method="https")

    assert result["success"] is True
    assert anon_closed is True
    assert auth_called is True
    assert result["files_count"] == 1


def test_update_uploads_never_target_the_existing_dataset_directory(monkeypatch, tmp_path):
    """An update's new files go to a fresh upload location, not /mdf_open/{source_id}/."""
    from mdf.core import agent as agent_module

    local_file = tmp_path / "data.csv"
    local_file.write_text("a,b,c")
    agent = MDFAgent()
    monkeypatch.setattr(
        agent,
        "build_submission",
        lambda test=False, update=False: {
            "source_id": "ds_one",
            "update": True,
            "title": "T",
            "authors": [{"name": "A"}],
            "data_sources": [str(local_file)],
            "extensions": {"mdf_source_id": "ds_one"},  # legacy key must not steer uploads
        },
    )
    seen: Dict[str, Any] = {}

    def fake_upload(data_sources, data_token, source_id=None, **kwargs):
        seen["source_id"] = source_id
        return agent_module.UploadResult(["globus://x/tmp/_uploads/abc/data.csv"], None, None)

    monkeypatch.setattr(agent_module, "_upload_local_files", fake_upload)

    class _Client:
        _globus_data_token = "dt"
        _globus_transfer_token = "tt"

        def auth_check(self):
            return {"is_submitter": True}

        def close(self):
            pass

        def submit(self, payload):
            return {"success": True, "source_id": "ds_one", "version": "2.0"}

    monkeypatch.setattr("mdf.core.agent.BackendClient.authenticated", lambda **kwargs: _Client())
    assert agent.publish(update=True, dry_run=False, token="tok")["success"] is True
    assert seen["source_id"] is None
