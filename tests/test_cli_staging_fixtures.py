"""Read commands against recorded staging responses (CLI-D5 / plan C3).

The fixtures under ``tests/fixtures/staging/`` are real ``GET`` responses from
the staging API (recorded 2026-09-25). Hand-written fixtures without authors are
how the `mdf show` crash on object-shaped authors (CLI-U1) shipped green, so
these tests render the live shapes.
Re-record with plain GETs against
https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging
(/search, /citation/{id}?format=all, /versions/{id}, /card/{id}).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from typer.testing import CliRunner

from mdf.cli.main import app
from mdf.core.backend_client import ApiResult

FIXTURES = Path(__file__).parent / "fixtures" / "staging"
IDS = ("bennett_origin_metalinsulator_metals", "9bb8800f-9cda-4957-ad12-60ad2a381177")
BOX_CHARS = set("╭╮╰╯│─━┃")

runner = CliRunner()


def fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class RecordedStaging:
    """Answers the read endpoints from the recorded responses."""

    def close(self) -> None:
        return None

    def _recorded(self, kind: str, source_id: str) -> ApiResult:
        path = FIXTURES / f"{kind}_{source_id}.json"
        if not path.exists():
            return ApiResult({"detail": "Dataset not found"}, status_code=404)
        return ApiResult(json.loads(path.read_text()), status_code=200)

    def get_card(self, source_id, version=None):
        return self._recorded("card", source_id)

    def get_citation(self, source_id, format="all", version=None):
        # The backend returns every format; the CLI picks one.
        return self._recorded("citation", source_id)

    def versions(self, source_id, limit=50, offset=0):
        return self._recorded("versions", source_id)

    def search(self, query=None, search_type="all", limit=20, **kwargs):
        if search_type == "datasets":  # DOI -> source_id resolution
            return ApiResult({"results": []}, status_code=200)
        return ApiResult(fixture("search_perovskite"), status_code=200)


@pytest.fixture(autouse=True)
def recorded_staging(monkeypatch):
    client = RecordedStaging()
    for module in ("mdf.cli.main", "mdf.cli.dataset_cmd"):
        monkeypatch.setattr(f"{module}.read_client", lambda **kwargs: client)
    return client


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_id", IDS)
def test_show_renders_object_authors(source_id):
    card = fixture(f"card_{source_id}")["card"]
    result = runner.invoke(app, ["show", source_id, "--cite"])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    for author in card["authors"]:
        assert author["family_name"] in result.stdout
    assert "{'name'" not in result.stdout
    # Piped (CliRunner is not a TTY): no box drawing.
    assert not BOX_CHARS & set(result.stdout)


@pytest.mark.parametrize("source_id", IDS)
def test_show_json_is_the_flat_card(source_id):
    result = runner.invoke(app, ["show", source_id, "--json", "--cite"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["card"]["source_id"] == source_id
    assert "card" not in payload["card"]
    assert payload["citation"]["source_id"] == source_id


def test_show_unknown_id_is_not_found():
    result = runner.invoke(app, ["show", "no_such_dataset", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["kind"] == "not_found"


# ---------------------------------------------------------------------------
# cite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["bibtex", "ris", "apa", "datacite"])
def test_cite_piped_is_the_raw_citation(fmt):
    source_id = IDS[0]
    expected = fixture(f"citation_{source_id}")[fmt].rstrip("\n")
    result = runner.invoke(app, ["dataset", "cite", source_id, "-f", fmt])
    assert result.exit_code == 0, result.output
    assert result.stdout == expected + "\n"


def test_cite_writes_a_file(tmp_path):
    source_id = IDS[0]
    out = tmp_path / "ref.bib"
    result = runner.invoke(app, ["dataset", "cite", source_id, "-f", "bibtex", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text() == fixture(f"citation_{source_id}")["bibtex"].rstrip("\n") + "\n"
    assert result.stdout == ""


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_cite_rejects_unknown_formats(extra):
    result = runner.invoke(app, ["dataset", "cite", IDS[0], "-f", "nonsense", *extra])
    assert result.exit_code == 2
    if extra:
        assert json.loads(result.stdout)["error"]["kind"] == "validation"


# ---------------------------------------------------------------------------
# versions / search
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_id", IDS)
def test_versions(source_id):
    recorded = fixture(f"versions_{source_id}")
    human = runner.invoke(app, ["dataset", "versions", source_id])
    assert human.exit_code == 0, human.output
    assert recorded["dataset_doi"] in human.stdout
    payload = json.loads(runner.invoke(app, ["dataset", "versions", source_id, "--json"]).stdout)
    assert payload["versions"] == recorded["versions"]


def test_search_shows_full_ids_first():
    recorded = fixture("search_perovskite")
    result = runner.invoke(app, ["search", "perovskite"], env={"COLUMNS": "80"})
    assert result.exit_code == 0, result.output
    rows = result.stdout.splitlines()
    for hit in recorded["results"]:
        # Every ID appears whole, at the start of its row.
        assert any(row.startswith(hit["source_id"] + " ") for row in rows), hit["source_id"]
    payload = json.loads(runner.invoke(app, ["search", "perovskite", "--json"]).stdout)
    assert [r["source_id"] for r in payload["results"]] == [r["source_id"] for r in recorded["results"]]
