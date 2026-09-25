"""Manifest file handling for mdf.yaml.

This module provides functions for reading, writing, and initializing
mdf.yaml manifest files. The manifest is a YAML file that declares
dataset metadata in a human-readable format.

Example mdf.yaml::

    title: "My Dataset"
    authors:
      - name: "Jane Doe"
        affiliations: ["MIT"]
      - "John Smith"
    description: "A test dataset"
    data_sources:
      - "./data"
      - "globus://endpoint/path"
    acl: ["public"]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

import yaml

from mdf.models.config import Author, DataSource, ManifestConfig


def _normalize_authors(authors: List[Union[str, Dict[str, Any]]]) -> List[Union[str, Author]]:
    normalized: List[Union[str, Author]] = []
    for author in authors:
        if isinstance(author, Author) or isinstance(author, str):
            normalized.append(author)
        elif isinstance(author, dict):
            normalized.append(Author(**author))
        else:
            normalized.append(str(author))
    return normalized


def _normalize_data_sources(
    data_sources: List[Union[str, Dict[str, Any]]]
) -> List[Union[str, DataSource]]:
    normalized: List[Union[str, DataSource]] = []
    for source in data_sources:
        if isinstance(source, DataSource) or isinstance(source, str):
            normalized.append(source)
        elif isinstance(source, dict):
            normalized.append(DataSource(**source))
        else:
            normalized.append(str(source))
    return normalized


def load_manifest(path: Path) -> ManifestConfig:
    """Load a manifest from a YAML file.

    Parses the YAML file and normalizes authors and data_sources
    into their proper Pydantic model types.

    Args:
        path: Path to the mdf.yaml file.

    Returns:
        ManifestConfig populated from the YAML file.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        yaml.YAMLError: If the file is not valid YAML.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "authors" in data and data["authors"] is not None:
        authors = data["authors"]
        if not isinstance(authors, list):
            authors = [authors]
        data["authors"] = _normalize_authors(authors)
    if "data_sources" in data and data["data_sources"] is not None:
        data_sources = data["data_sources"]
        if not isinstance(data_sources, list):
            data_sources = [data_sources]
        data["data_sources"] = _normalize_data_sources(data_sources)
    return ManifestConfig(**data)


def save_manifest(config: ManifestConfig, path: Path) -> None:
    """Save a manifest to a YAML file.

    Serializes the ManifestConfig to YAML format, excluding None values
    for cleaner output.

    Args:
        config: The ManifestConfig to save.
        path: Path where the YAML file will be written.
    """
    data = config.model_dump(exclude_none=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def init_manifest(
    path: Path,
    title: str,
    authors: List[str],
    description: str | None = None,
    publisher: str | None = None,
    publication_year: int | str | None = None,
) -> ManifestConfig:
    """Create and save a new manifest file.

    Creates a ManifestConfig with the provided metadata and saves it
    to the specified path.

    Args:
        path: Path where the mdf.yaml file will be created.
        title: Dataset title.
        authors: List of author names.
        description: Optional dataset description.
        publisher: Optional publisher name (defaults to MDF).
        publication_year: Optional publication year.

    Returns:
        The created ManifestConfig.
    """
    config = ManifestConfig(
        title=title,
        authors=authors,
        description=description,
        publisher=publisher,
        publication_year=publication_year,
    )
    save_manifest(config, path)
    return config


# ---------------------------------------------------------------------------
# Updates: previous metadata -> manifest, and the source_id write-back
# ---------------------------------------------------------------------------

#: Flat v2 metadata key -> ManifestConfig field, for the fields a new version
#: inherits. The reverse of ManifestConfig.to_metadata_payload. Server-managed
#: keys (version, latest, doi, data_sources, download_url, acl, ...) are
#: deliberately absent: the server carries them forward itself.
_INHERITED_METADATA_FIELDS: Dict[str, str] = {
    "title": "title",
    "authors": "authors",
    "description": "description",
    "publisher": "publisher",
    "publication_year": "publication_year",
    "resource_type": "resource_type",
    "keywords": "subjects",
    "organization": "organization",
    "tags": "tags",
    "domains": "domains",
    "license": "license",
    "funding": "funding",
    "related_works": "related_works",
    "methods": "methods",
    "facility": "facility",
    "fields_of_science": "fields_of_science",
    "ml": "ml",
    "geo_locations": "geo_locations",
    "data_type": "data_type",
    "formats": "formats",
    "external_doi": "external_doi",
    "external_url": "external_url",
    "external_source": "external_source",
}


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def manifest_from_metadata(metadata: Dict[str, Any]) -> ManifestConfig:
    """Build a manifest from a published record's flat v2 metadata.

    Used by ``mdf publish --update``: the previous version's full metadata
    (``dataset_mdata`` from ``GET /status/{source_id}``) becomes the base that
    the user's changes are laid over, so a new version keeps its description,
    keywords, license, funding and related works.
    """
    fields: Dict[str, Any] = {}
    for key, field in _INHERITED_METADATA_FIELDS.items():
        value = metadata.get(key)
        if not _is_blank(value):
            fields[field] = value

    # The server folds external_* into one `external` object.
    external = metadata.get("external")
    if isinstance(external, dict):
        for key in ("doi", "url", "source"):
            if external.get(key) and "external_" + key not in fields:
                fields["external_" + key] = external[key]

    # User extensions survive; reserved `mdf_*` keys (identity) never do.
    extensions = metadata.get("extensions")
    if isinstance(extensions, dict):
        custom = {k: v for k, v in extensions.items() if not str(k).startswith("mdf_")}
        if custom:
            fields["custom"] = custom

    if isinstance(fields.get("authors"), list):
        fields["authors"] = _normalize_authors(fields["authors"])
    return ManifestConfig(**fields)


def merge_manifests(base: ManifestConfig, overrides: ManifestConfig) -> ManifestConfig:
    """Lay ``overrides`` over ``base``: every non-blank override field wins."""
    merged = base.model_dump(exclude_none=True)
    for key, value in overrides.model_dump(exclude_none=True).items():
        if not _is_blank(value):
            merged[key] = value
    if isinstance(merged.get("authors"), list):
        merged["authors"] = _normalize_authors(merged["authors"])
    if isinstance(merged.get("data_sources"), list):
        merged["data_sources"] = _normalize_data_sources(merged["data_sources"])
    return ManifestConfig(**merged)


def write_source_id(path: Path, source_id: str) -> bool:
    """Record ``source_id`` in an existing mdf.yaml without rewriting the rest.

    Appends one ``source_id:`` line, so comments and formatting survive, then
    re-reads the file to prove it still parses to the same manifest plus the
    id; otherwise the original text is restored. Returns True when written,
    False when the file already names a source_id or could not be updated.
    """
    original = path.read_text(encoding="utf-8")
    data = yaml.safe_load(original) or {}
    if not isinstance(data, dict) or data.get("source_id"):
        return False
    separator = "" if original.endswith("\n") or not original else "\n"
    path.write_text(
        f"{original}{separator}"
        "# Set by `mdf publish`; `mdf publish --update` adds a version to this dataset.\n"
        + yaml.safe_dump({"source_id": source_id}),
        encoding="utf-8",
    )
    try:
        written = yaml.safe_load(path.read_text(encoding="utf-8"))
        ok = isinstance(written, dict) and written.get("source_id") == source_id and all(
            written.get(key) == value for key, value in data.items()
        )
    except yaml.YAMLError:
        ok = False
    if not ok:
        path.write_text(original, encoding="utf-8")
    return ok
