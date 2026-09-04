from __future__ import annotations

import json

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
    monkeypatch.setattr("mdf.cli.main.MDFAgent.plan_clone", lambda self, **kwargs: plan)

    def must_not_clone(self, **kwargs):
        raise AssertionError("clone downloaded data in plan mode")

    monkeypatch.setattr("mdf.cli.main.MDFAgent.clone", must_not_clone)
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
