"""Tests for manifest DOI/dc passthrough (Lane C2)."""

from pathlib import Path
import pytest

from mdf.models.config import ManifestConfig
from mdf.core.submission import build_submission


class TestManifestConfigDoiAndDc:
    """Tests for dataset_doi and dc handling in ManifestConfig and build_submission."""

    def test_dataset_doi_dropped_with_warning_when_no_external_source(self):
        """R2-15: dataset_doi is this dataset's own DOI, not mirror provenance.

        `DatasetMetadata` (connect_server aws/v2/metadata.py) has no
        `dataset_doi` field and folds `external_doi` into `ExternalSource`
        with `source="Unknown"`, so emitting it unconditionally fabricated a
        "mirror of another repository" claim. Drop it, loudly.
        """
        manifest = ManifestConfig(
            title="Test Dataset",
            authors=["Curie, Marie"],
            dataset_doi="10.1234/manifest-dataset-doi",
        )
        with pytest.warns(UserWarning, match="dataset_doi' was dropped"):
            payload = manifest.to_metadata_payload()
        assert "external_doi" not in payload

    def test_dataset_doi_forwarded_when_external_source_declared(self):
        manifest = ManifestConfig(
            title="Test Dataset",
            authors=["Curie, Marie"],
            dataset_doi="10.1234/manifest-dataset-doi",
            external_source="Zenodo",
        )
        payload = manifest.to_metadata_payload()
        assert payload.get("external_doi") == "10.1234/manifest-dataset-doi"
        assert payload.get("external_source") == "Zenodo"

    def test_dataset_doi_forwarded_when_external_url_declared(self):
        manifest = ManifestConfig(
            title="Test Dataset",
            authors=["Curie, Marie"],
            dataset_doi="10.1234/manifest-dataset-doi",
            external_url="https://zenodo.org/record/1234567",
        )
        payload = manifest.to_metadata_payload()
        assert payload.get("external_doi") == "10.1234/manifest-dataset-doi"

    def test_to_metadata_payload_preserves_explicit_external_doi(self):
        manifest = ManifestConfig(
            title="Test Dataset",
            authors=["Curie, Marie"],
            external_doi="10.5678/explicit-external-doi",
        )
        payload = manifest.to_metadata_payload()
        assert payload.get("external_doi") == "10.5678/explicit-external-doi"

    def test_to_metadata_payload_external_doi_precedence(self):
        manifest = ManifestConfig(
            title="Test Dataset",
            authors=["Curie, Marie"],
            external_doi="10.5678/explicit-external-doi",
            dataset_doi="10.1234/manifest-dataset-doi",
        )
        payload = manifest.to_metadata_payload()
        assert payload.get("external_doi") == "10.5678/explicit-external-doi"

    def test_to_metadata_payload_emits_dc(self):
        dc_data = {
            "titles": [{"title": "Legacy DC Title"}],
            "creators": [{"creatorName": "Legacy Creator"}],
        }
        manifest = ManifestConfig(
            title="Test Dataset",
            authors=["Curie, Marie"],
            dc=dc_data,
        )
        payload = manifest.to_metadata_payload()
        assert payload.get("dc") == dc_data

    def test_build_submission_drops_dataset_doi_and_passes_dc(self, tmp_path: Path):
        dc_data = {
            "titles": [{"title": "Legacy Title"}],
            "creators": [{"creatorName": "Legacy Creator"}],
        }
        test_file = tmp_path / "sample.csv"
        test_file.write_text("a,b,c\n1,2,3\n")

        manifest = ManifestConfig(
            title="Test Submission",
            authors=["Curie, Marie"],
            dataset_doi="10.1234/test-dataset-doi",
            dc=dc_data,
            data_sources=[str(test_file)],
        )

        with pytest.warns(UserWarning, match="dataset_doi' was dropped"):
            payload = build_submission(manifest, root=tmp_path)
        # R2-15: not smuggled in as external provenance.
        assert "external_doi" not in payload
        assert payload.get("dc") == dc_data

    def test_build_submission_omits_doi_and_dc_when_not_set(self, tmp_path: Path):
        test_file = tmp_path / "sample.csv"
        test_file.write_text("a,b,c\n1,2,3\n")

        manifest = ManifestConfig(
            title="Clean Submission",
            authors=["Curie, Marie"],
            data_sources=[str(test_file)],
        )

        payload = build_submission(manifest, root=tmp_path)
        assert "external_doi" not in payload
        assert "dc" not in payload
