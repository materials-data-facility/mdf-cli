"""Tests for manifest DOI/dc passthrough and stream-tool gating (Lane C2)."""

from pathlib import Path
import pytest

from mdf.models.config import ManifestConfig
from mdf.core.submission import build_submission
from mdf.skill.mdf_tools import TOOLS
from mdf.skill import handlers
from mdf.skill.handlers import FeatureUnavailableError

DISABLED_STREAM_MSG = (
    "Streams are disabled in this release; publish by reference to Globus/HTTPS data_sources"
)


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


class TestStreamToolsRegistry:
    """Tests that stream tools are removed from TOOLS registry."""

    def test_stream_tools_not_in_registry(self):
        removed_tools = [
            "mdf_stream_create",
            "mdf_stream_append",
            "mdf_stream_status",
            "mdf_stream_close",
            "mdf_stream_snapshot",
        ]
        for tool_name in removed_tools:
            assert tool_name not in TOOLS, f"{tool_name} should be removed from TOOLS"

    def test_other_tools_remain_registered(self):
        expected_tools = [
            "mdf_health_check",
            "mdf_scan_folder",
            "mdf_create_manifest",
            "mdf_suggest_mappings",
            "mdf_validate_and_preview",
            "mdf_publish",
            "mdf_check_status",
            "mdf_list_submissions",
            "mdf_search_datasets",
        ]
        for tool_name in expected_tools:
            assert tool_name in TOOLS, f"{tool_name} should remain in TOOLS"


class TestStreamHandlersReturnFeatureUnavailableEnvelope:
    """Stream handlers are @agent_safe: they report, they never raise.

    Skill handlers are called directly by agents, so a raised exception is a
    crash rather than a usable answer. `agent_safe` turns
    `FeatureUnavailableError` into the standard envelope with
    `kind == "feature_unavailable"`.
    """

    @pytest.mark.parametrize(
        "call",
        [
            lambda: handlers.stream_create("title"),
            lambda: handlers.stream_create(),
            lambda: handlers.stream_append("stream-123", files=["foo.csv"]),
            lambda: handlers.stream_status("stream-123"),
            lambda: handlers.stream_close("stream-123"),
            lambda: handlers.stream_snapshot("stream-123"),
        ],
        ids=["create", "create_no_args", "append", "status", "close", "snapshot"],
    )
    def test_stream_handler_returns_envelope(self, call):
        result = call()
        assert result["success"] is False
        assert result["kind"] == "feature_unavailable"
        assert result["error"] == DISABLED_STREAM_MSG
        assert result["error_type"] == "FeatureUnavailableError"

    def test_feature_unavailable_error_is_still_exported(self):
        assert issubclass(FeatureUnavailableError, Exception)
