from __future__ import annotations

import json
import time

import pytest
from typer.testing import CliRunner

from mdf.cli.main import app


runner = CliRunner()


@pytest.fixture
def clone_plan(monkeypatch: pytest.MonkeyPatch):
    plan = {
        "success": True,
        "requested_identifier": "dataset-1",
        "resolved_source_id": "dataset-1",
        "version": "1.2",
        "title": "Dataset One",
        "output_path": "./dataset-1",
        "selected_method": "https",
        "file_count": 3,
        "total_bytes": 4096,
        "auth_needed": False,
    }
    monkeypatch.setattr("mdf.core.agent.MDFAgent.plan_clone", lambda self, **kwargs: plan)

    def must_not_clone(self, **kwargs):
        raise AssertionError("clone downloaded data in plan mode")

    monkeypatch.setattr("mdf.core.agent.MDFAgent.clone", must_not_clone)
    return plan


@pytest.mark.parametrize("flag", ["--plan", "--dry-run"])
def test_clone_plan_does_not_download_and_renders_table(flag: str, clone_plan):
    result = runner.invoke(app, ["clone", "dataset-1", flag])
    assert result.exit_code == 0
    assert "Clone plan" in result.stdout
    assert "Direct file download" in result.stdout
    assert "3" in result.stdout
    assert "4.0 KB" in result.stdout
    assert "Auth needed?" in result.stdout


def test_clone_plan_json_uses_success_envelope(clone_plan):
    result = runner.invoke(app, ["clone", "dataset-1", "--plan", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {"success": True, "plan": clone_plan}


def test_clone_help_advertises_plan():
    result = runner.invoke(app, ["clone", "--help"])
    assert result.exit_code == 0
    assert "--plan" in result.stdout
    assert "--dry-run" in result.stdout


def test_plan_does_not_create_the_output_directory(tmp_path, monkeypatch):
    """--plan is a read-only preview: no directory may appear on disk."""
    from mdf.core import agent as agent_module

    fake_card = {"source_id": "ds_v1.0", "title": "T", "data_sources": ["https://example.org/a.csv"]}

    class FakeBackendClient:
        _globus_data_token = None
        _globus_transfer_token = None

        def __init__(self, *a, **k):
            pass

        @classmethod
        def authenticated(cls, **kw):
            return cls()

        def get_card(self, source_id, version=None):
            return fake_card

        def close(self):
            pass

    monkeypatch.setattr(agent_module, "BackendClient", FakeBackendClient)
    out = tmp_path / "would-be-created"
    agent_module.MDFAgent().plan_clone(source_id="ds_v1.0", output_dir=str(out))
    assert not out.exists()


# ---------------------------------------------------------------------------
# C5 / CLI-U3: card-only plans, bounded listings, honest login messages
# ---------------------------------------------------------------------------

NCSA = "82f1b5c6-6e9b-11e5-ba47-22000b92c6ec"
DIR_CARD = {
    "source_id": "dir_ds",
    "title": "Directory dataset",
    "download_url": "https://data.materialsdatafacility.org/mdf_open/dir_ds/",
    "data_sources": [f"globus://{NCSA}/mdf_open/dir_ds/"],
    "stats": {"file_count": 12, "total_bytes": 2048},
}


def _card_only_backend(monkeypatch, card=DIR_CARD, transfer_token=None):
    from mdf.core import agent as agent_module

    class FakeBackendClient:
        _globus_data_token = None
        _globus_transfer_token = transfer_token

        def __init__(self, *a, **k):
            pass

        @classmethod
        def authenticated(cls, **kw):
            return cls()

        def get_card(self, source_id, version=None):
            return {"card": card} if source_id == card["source_id"] else {"detail": "Dataset not found"}

        def close(self):
            pass

    monkeypatch.setattr(agent_module, "BackendClient", FakeBackendClient)
    return agent_module


def test_plan_reads_the_card_only(monkeypatch, tmp_path):
    agent_module = _card_only_backend(monkeypatch)

    def no_listing(*args, **kwargs):
        raise AssertionError("a plain --plan must not list files")

    monkeypatch.setattr(agent_module, "_resolve_globus_files", no_listing)
    plan = agent_module.MDFAgent().plan_clone(source_id="dir_ds", output_dir=str(tmp_path / "o"))
    assert plan["success"] is True
    assert plan["file_count"] == 12 and plan["total_bytes"] == 2048
    assert plan["auth_needed"] is True  # no archive: download lists via Globus Transfer
    assert "files" not in plan


def test_plan_files_needs_a_login_and_says_so(monkeypatch, tmp_path):
    from mdf.core.exceptions import AuthRequired

    agent_module = _card_only_backend(monkeypatch)
    with pytest.raises(AuthRequired, match="Globus login"):
        agent_module.MDFAgent().plan_clone(
            source_id="dir_ds", output_dir=str(tmp_path / "o"), list_files=True
        )


def test_plan_files_lists_with_progress(monkeypatch, tmp_path):
    import globus_sdk

    agent_module = _card_only_backend(monkeypatch, transfer_token="tt")

    class FakeTC:
        def __init__(self, authorizer=None, **kwargs):
            pass

        def operation_ls(self, endpoint_id, path=None):
            return [{"type": "file", "name": "a.csv"}, {"type": "file", "name": "b.csv"}]

    monkeypatch.setattr(globus_sdk, "TransferClient", FakeTC)
    counts = []
    plan = agent_module.MDFAgent().plan_clone(
        source_id="dir_ds", output_dir=str(tmp_path / "o"), list_files=True, on_listing=counts.append
    )
    assert plan["files"] == ["a.csv", "b.csv"]
    assert plan["file_count"] == 2
    assert counts == [1, 2]


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_failed_or_slow_listing_is_an_error(monkeypatch, failure):
    import globus_sdk

    from mdf.core import agent as agent_module

    class FakeTC:
        def __init__(self, authorizer=None, **kwargs):
            pass

        def operation_ls(self, endpoint_id, path=None):
            if failure == "error":
                raise RuntimeError("boom")
            # Endless tree: only the deadline stops it. Each listing takes time, like a
            # real Transfer call, so the deadline trips long before the recursion limit.
            time.sleep(0.005)
            return [{"type": "dir", "name": "sub"}]

    monkeypatch.setattr(globus_sdk, "TransferClient", FakeTC)
    with pytest.raises(agent_module.CloneListingError):
        agent_module._resolve_globus_files(
            [f"globus://{NCSA}/mdf_open/x/"], "tt", timeout=0.05 if failure == "timeout" else None
        )


def test_private_dataset_plan_says_why_a_login_is_needed(monkeypatch, tmp_path):
    from mdf.core import agent as agent_module
    from mdf.core.exceptions import AuthRequired

    _card_only_backend(monkeypatch)

    def no_login(**kwargs):
        raise AuthRequired()

    monkeypatch.setattr(agent_module.BackendClient, "authenticated", staticmethod(no_login))
    with pytest.raises(AuthRequired, match="not publicly readable"):
        agent_module.MDFAgent().plan_clone(source_id="secret_ds", output_dir=str(tmp_path / "o"))


def test_cli_plan_listing_failure_exits_non_zero_with_json(monkeypatch):
    from mdf.core.agent import CloneListingError

    def failing_plan(self, **kwargs):
        assert kwargs["list_files"] is True
        raise CloneListingError("Could not list /mdf_open/x/: boom")

    monkeypatch.setattr("mdf.core.agent.MDFAgent.plan_clone", failing_plan)
    result = runner.invoke(app, ["clone", "x", "--plan", "--files", "--json"])
    assert result.exit_code == 5
    assert "boom" in json.loads(result.stdout)["error"]["message"]
