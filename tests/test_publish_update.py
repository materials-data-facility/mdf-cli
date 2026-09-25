"""`mdf publish --update [ID]` (CLI-U4 / plan C4) and the staging DOI notice (C10a).

An update must: find the dataset on its own (argument, then mdf.yaml, then the
last publish), start from the previous version's *full* metadata, send the
top-level ``source_id`` (never ``extensions.mdf_source_id``), and the first
publish must write ``source_id`` back into mdf.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml
from typer.testing import CliRunner

from mdf.cli.main import app
from mdf.core.backend_client import ApiResult
from mdf.core.manifest import load_manifest, manifest_from_metadata, merge_manifests, write_source_id
from mdf.core.submission import build_submission
from mdf.models.config import Author, ManifestConfig

runner = CliRunner()

#: The previous version as `GET /status/{id}` returns it to its owner.
PREVIOUS_MDATA: Dict[str, Any] = {
    "title": "Old title",
    "authors": [
        {"name": "Doe, Jane", "given_name": "Jane", "family_name": "Doe", "affiliations": ["UChicago"]}
    ],
    "description": "The original description.",
    "keywords": ["perovskite", "DFT"],
    "license": {"name": "CC-BY-4.0", "identifier": "CC-BY-4.0"},
    "funding": [{"funder_name": "NSF", "award_number": "123"}],
    "related_works": [
        {"identifier": "10.1000/paper", "identifier_type": "DOI", "relation_type": "IsSupplementTo"}
    ],
    "extensions": {"drafted_by": "agent", "mdf_source_id": "legacy-key"},
    # Server-managed: must never be copied into the new version's payload.
    "version": "1.0",
    "latest": True,
    "data_sources": ["globus://ep/mdf_open/ds_one/1.0/"],
    "download_url": "https://example.org/ds_one.zip",
}


class _StatusClient:
    def __init__(self, source_id: str = "ds_one", mdata: Dict[str, Any] = PREVIOUS_MDATA):
        self.source_id = source_id
        self.mdata = mdata
        self.status_calls: List[str] = []

    def close(self) -> None:
        return None

    def status(self, source_id, version=None):
        self.status_calls.append(source_id)
        if source_id != self.source_id:
            return ApiResult({"success": False, "error": "Submission not found"}, status_code=200)
        return ApiResult(
            {
                "success": True,
                "submission": {
                    "source_id": self.source_id,
                    "version": "1.0",
                    "status": "published",
                    "dataset_mdata": json.dumps(self.mdata),
                },
            },
            status_code=200,
        )


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("MDF_SERVICE", "MDF_API_URL"):
        monkeypatch.delenv(name, raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


@pytest.fixture
def status_client(monkeypatch):
    client = _StatusClient()
    monkeypatch.setattr("mdf.core.backend_client.BackendClient.authenticated", lambda **kwargs: client)
    return client


def _dry_run_payload(args: List[str]) -> Dict[str, Any]:
    result = runner.invoke(app, ["publish", *args, "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["dry_run"] is True
    return body["payload"]


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def test_manifest_from_metadata_keeps_user_metadata_and_drops_server_fields():
    manifest = manifest_from_metadata(PREVIOUS_MDATA)
    assert manifest.subjects == ["perovskite", "DFT"]
    assert manifest.license == {"name": "CC-BY-4.0", "identifier": "CC-BY-4.0"}
    assert manifest.funding == PREVIOUS_MDATA["funding"]
    assert manifest.related_works == PREVIOUS_MDATA["related_works"]
    assert isinstance(manifest.authors[0], Author)
    assert manifest.custom == {"drafted_by": "agent"}  # mdf_* identity keys dropped
    assert manifest.data_sources == []
    dumped = manifest.model_dump(exclude_none=True)
    assert "version" not in dumped and "download_url" not in dumped


def test_merge_lets_every_set_field_win():
    merged = merge_manifests(
        manifest_from_metadata(PREVIOUS_MDATA), ManifestConfig(title="New title")
    )
    assert merged.title == "New title"
    assert merged.description == "The original description."


def test_update_payload_sends_top_level_source_id_only(tmp_path):
    manifest = ManifestConfig(source_id="ds_one", title="T", authors=["A"], update_metadata_only=True)
    (tmp_path / "stray.csv").write_text("x\n")  # must not be auto-uploaded on an update
    payload = build_submission(manifest, tmp_path, update=True)
    assert payload["source_id"] == "ds_one"
    assert "mdf_source_id" not in (payload.get("extensions") or {})
    assert payload["data_sources"] == []

    # A first publish never sends an identity.
    assert "source_id" not in build_submission(manifest, tmp_path, update=False)


def test_write_source_id_appends_and_keeps_comments(tmp_path):
    path = tmp_path / "mdf.yaml"
    path.write_text("# my dataset\ntitle: T  # keep me\nauthors:\n  - A\n")
    assert write_source_id(path, "ds_one") is True
    text = path.read_text()
    assert text.startswith("# my dataset\ntitle: T  # keep me\n")
    assert load_manifest(path).source_id == "ds_one"
    # Never overwrites an existing id.
    assert write_source_id(path, "other") is False
    assert yaml.safe_load(path.read_text())["source_id"] == "ds_one"


# ---------------------------------------------------------------------------
# ID resolution
# ---------------------------------------------------------------------------


def test_update_id_from_argument(workdir, status_client):
    payload = _dry_run_payload(["--update", "ds_one"])
    assert status_client.status_calls == ["ds_one"]
    assert payload["source_id"] == "ds_one"
    assert payload["update"] is True


def test_update_id_from_mdf_yaml(workdir, status_client):
    (workdir / "mdf.yaml").write_text("source_id: ds_one\ntitle: Local title\n")
    payload = _dry_run_payload(["--update"])
    assert payload["source_id"] == "ds_one"
    assert payload["title"] == "Local title"  # the user's change wins


def test_update_id_from_last_publish(workdir, status_client):
    from mdf.core.config import GlobalConfig

    GlobalConfig().record_publish("ds_one", "1.0", "staging")
    payload = _dry_run_payload(["--update"])
    assert payload["source_id"] == "ds_one"


def test_update_without_any_id_is_a_usage_error(workdir, status_client):
    result = runner.invoke(app, ["publish", "--update", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["kind"] == "validation"


def test_existing_path_after_update_is_data_not_an_id(workdir, status_client):
    (workdir / "new_data").mkdir()
    (workdir / "new_data" / "a.csv").write_text("x\n")
    (workdir / "mdf.yaml").write_text("source_id: ds_one\n")
    payload = _dry_run_payload(["--update", "new_data"])
    assert payload["source_id"] == "ds_one"
    assert any(src.endswith("new_data") for src in payload["data_sources"])
    assert payload["update_metadata_only"] is False


# ---------------------------------------------------------------------------
# Metadata is preserved
# ---------------------------------------------------------------------------


def test_update_keeps_previous_metadata(workdir, status_client):
    payload = _dry_run_payload(["--update", "ds_one", "--title", "New title"])
    assert payload["title"] == "New title"
    assert payload["description"] == "The original description."
    assert payload["keywords"] == ["perovskite", "DFT"]
    assert payload["license"]["identifier"] == "CC-BY-4.0"
    assert payload["funding"] == PREVIOUS_MDATA["funding"]
    assert payload["related_works"] == PREVIOUS_MDATA["related_works"]
    assert payload["authors"][0]["affiliations"] == ["UChicago"]
    # Metadata-only: no data, and the old files are the server's to carry over.
    assert payload["data_sources"] == []
    assert payload["update_metadata_only"] is True
    assert "mdf_source_id" not in (payload.get("extensions") or {})


def test_update_of_unknown_dataset_fails_with_not_found(workdir, status_client):
    result = runner.invoke(app, ["publish", "--update", "nope", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["kind"] == "not_found"


# ---------------------------------------------------------------------------
# Submit: write-back, duplicate guard, staging notice
# ---------------------------------------------------------------------------


def _stub_submit(monkeypatch, response: Dict[str, Any]):
    monkeypatch.setattr(
        "mdf.cli.commands.publish.run_preflight",
        lambda *args, **kwargs: {"success": True, "issues": [], "source_summary": {}},
    )
    monkeypatch.setattr("mdf.core.agent.MDFAgent.publish", lambda self, **kwargs: dict(response))


def test_first_publish_writes_source_id_back_and_warns_about_test_dois(workdir, monkeypatch):
    (workdir / "mdf.yaml").write_text("title: T\nauthors:\n  - A\n")
    (workdir / "data.csv").write_text("a\n1\n")
    _stub_submit(monkeypatch, {"success": True, "source_id": "new_ds", "version": "1.0"})
    result = runner.invoke(app, ["publish", "--submit", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["source_id"] == "new_ds"  # payload shape unchanged
    assert load_manifest(workdir / "mdf.yaml").source_id == "new_ds"
    assert "10.23677" in result.stderr and "do not resolve" in result.stderr


def test_published_manifest_cannot_be_submitted_again_without_update(workdir, monkeypatch):
    (workdir / "mdf.yaml").write_text("source_id: ds_one\ntitle: T\nauthors:\n  - A\n")
    (workdir / "data.csv").write_text("a\n1\n")
    _stub_submit(monkeypatch, {"success": True, "source_id": "dup", "version": "1.0"})
    result = runner.invoke(app, ["publish", "--submit", "--json"])
    assert result.exit_code == 2
    assert "--update" in json.loads(result.stdout)["error"]["hint"]


def test_no_staging_notice_for_other_services(workdir, monkeypatch):
    (workdir / "data.csv").write_text("a\n1\n")
    _stub_submit(monkeypatch, {"success": True, "source_id": "new_ds", "version": "1.0"})
    result = runner.invoke(
        app,
        ["publish", "data.csv", "-t", "T", "-a", "A", "--submit", "--json", "--service", "local"],
    )
    assert result.exit_code == 0, result.output
    assert "10.23677" not in result.stderr


# ---------------------------------------------------------------------------
# Review fixes (cross-family review of e20c3a1)
# ---------------------------------------------------------------------------


def test_update_ignores_mdf_yaml_data_sources_without_new_paths(workdir, status_client):
    (workdir / "old_data").mkdir()
    (workdir / "old_data" / "a.csv").write_text("x\n")
    (workdir / "mdf.yaml").write_text("source_id: ds_one\ndata_sources:\n  - old_data\n")
    payload = _dry_run_payload(["--update"])
    assert payload["data_sources"] == []
    assert payload["update_metadata_only"] is True


def test_update_with_paths_still_applies_mdf_yaml(workdir, status_client):
    (workdir / "new_data").mkdir()
    (workdir / "new_data" / "a.csv").write_text("x\n")
    (workdir / "mdf.yaml").write_text("source_id: ds_one\ndescription: From yaml\n")
    payload = _dry_run_payload(["--update", "new_data", "--title", "Flag title"])
    assert payload["description"] == "From yaml"  # previous <- mdf.yaml
    assert payload["title"] == "Flag title"  # <- flags
    assert payload["keywords"] == ["perovskite", "DFT"]  # inherited
    assert payload["update_metadata_only"] is False


def test_explicit_empty_values_in_mdf_yaml_clear_inherited_ones(workdir, status_client):
    (workdir / "mdf.yaml").write_text(
        "source_id: ds_one\nsubjects: []\nrelated_works: []\ndescription:\n"
    )
    payload = _dry_run_payload(["--update"])
    assert "keywords" not in payload
    assert "related_works" not in payload
    assert "description" not in payload
    assert payload["funding"] == PREVIOUS_MDATA["funding"]  # untouched keys stay


def test_custom_extensions_merge_per_key(workdir, status_client):
    (workdir / "mdf.yaml").write_text("source_id: ds_one\ncustom:\n  instrument: XRD\n")
    payload = _dry_run_payload(["--update"])
    assert payload["extensions"] == {"drafted_by": "agent", "instrument": "XRD"}


def test_inherited_authors_keep_unknown_keys(workdir, monkeypatch):
    mdata = dict(PREVIOUS_MDATA)
    mdata["authors"] = [{"name": "Doe, Jane", "identifiers": [{"scheme": "ISNI", "id": "1"}]}]
    client = _StatusClient(mdata=mdata)
    monkeypatch.setattr("mdf.core.backend_client.BackendClient.authenticated", lambda **kwargs: client)
    payload = _dry_run_payload(["--update", "ds_one"])
    assert payload["authors"][0]["identifiers"] == [{"scheme": "ISNI", "id": "1"}]


@pytest.mark.parametrize("raw", [None, "{not json", ""])
def test_unreadable_previous_metadata_refuses_the_update(workdir, monkeypatch, raw):
    class BadClient(_StatusClient):
        def status(self, source_id, version=None):
            submission = {"source_id": "ds_one", "version": "1.0"}
            if raw is not None:
                submission["dataset_mdata"] = raw
            return ApiResult({"success": True, "submission": submission}, status_code=200)

    monkeypatch.setattr("mdf.core.backend_client.BackendClient.authenticated", lambda **kwargs: BadClient())
    result = runner.invoke(app, ["publish", "--update", "ds_one", "--json"])
    assert result.exit_code == 5
    assert json.loads(result.stdout)["error"]["kind"] == "server"


def test_write_source_id_replaces_a_null_key_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "mdf.yaml"
    path.write_text("title: T\nsource_id: null  # filled in by mdf publish\nauthors:\n  - A\n")
    assert write_source_id(path, "ds_one") is True
    text = path.read_text()
    assert text.count("source_id") == 1
    assert yaml.safe_load(text) == {"title": "T", "source_id": "ds_one", "authors": ["A"]}
    assert [p.name for p in tmp_path.iterdir()] == ["mdf.yaml"]


def test_history_failure_does_not_fail_a_publish(workdir, monkeypatch):
    (workdir / "mdf.yaml").write_text("title: T\nauthors:\n  - A\n")
    (workdir / "data.csv").write_text("a\n1\n")
    _stub_submit(monkeypatch, {"success": True, "source_id": "new_ds", "version": "1.0"})

    def broken_history(self, *args, **kwargs):
        raise OSError("read-only home")

    monkeypatch.setattr("mdf.core.config.GlobalConfig.record_publish", broken_history)
    result = runner.invoke(app, ["publish", "--submit", "--json"])
    assert result.exit_code == 0, result.output
    assert load_manifest(workdir / "mdf.yaml").source_id == "new_ds"
    assert "read-only home" in result.stderr
