"""Public DataCite DOI metadata adapter."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote, urlparse

import httpx

from mdf.core.agent import _DOI_RE
from mdf.importers.base import ExternalRecordNotFoundError, ExternalRepoAdapter


DATACITE_API = "https://api.datacite.org"


def _first_text(values: Any, key: str) -> str:
    if not isinstance(values, list):
        return ""
    for value in values:
        if isinstance(value, dict) and value.get(key):
            return str(value[key])
    return ""


def _affiliations(values: Any) -> List[str]:
    affiliations: List[str] = []
    for value in values or []:
        name = value.get("name") if isinstance(value, dict) else value
        if name and str(name) not in affiliations:
            affiliations.append(str(name))
    return affiliations


def _orcid(creator: Dict[str, Any]) -> Optional[str]:
    for identifier in creator.get("nameIdentifiers") or []:
        if not isinstance(identifier, dict):
            continue
        scheme = str(identifier.get("nameIdentifierScheme") or "").lower()
        url = str(identifier.get("schemeUri") or "").lower()
        if scheme == "orcid" or "orcid.org" in url:
            value = identifier.get("nameIdentifier")
            if value:
                return str(value).removeprefix("https://orcid.org/")
    return None


def _authors(creators: Any) -> List[Dict[str, Any]]:
    authors: List[Dict[str, Any]] = []
    for creator in creators or []:
        if not isinstance(creator, dict):
            continue
        author: Dict[str, Any] = {"name": str(creator.get("name") or "Unknown")}
        if creator.get("givenName"):
            author["given_name"] = str(creator["givenName"])
        if creator.get("familyName"):
            author["family_name"] = str(creator["familyName"])
        affiliations = _affiliations(creator.get("affiliation"))
        if affiliations:
            author["affiliations"] = affiliations
        orcid = _orcid(creator)
        if orcid:
            author["orcid"] = orcid
        authors.append(author)
    return authors or [{"name": "Unknown"}]


def _license(rights_list: Any) -> Optional[Dict[str, str]]:
    if not isinstance(rights_list, list):
        return None
    for rights in rights_list:
        if not isinstance(rights, dict):
            continue
        name = rights.get("rights") or rights.get("rightsIdentifier")
        identifier = rights.get("rightsIdentifier")
        url = rights.get("rightsUri")
        if name or identifier or url:
            return {
                key: str(value)
                for key, value in (("name", name), ("identifier", identifier), ("url", url))
                if value
            }
    return None


def _related_works(values: Any) -> List[Dict[str, str]]:
    works: List[Dict[str, str]] = []
    for related in values or []:
        if not isinstance(related, dict) or not related.get("relatedIdentifier"):
            continue
        work = {
            "identifier": str(related["relatedIdentifier"]),
            "identifier_type": str(related.get("relatedIdentifierType") or "Other"),
            "relation_type": str(related.get("relationType") or "References"),
        }
        if related.get("description"):
            work["description"] = str(related["description"])
        works.append(work)
    return works


def _content_urls(attributes: Dict[str, Any]) -> List[str]:
    values: List[Any] = []
    content = attributes.get("contentUrl")
    values.extend(content if isinstance(content, list) else [content])
    media = attributes.get("media") or []
    if isinstance(media, dict):
        media = [media]
    for item in media:
        values.append(item.get("url") if isinstance(item, dict) else item)

    urls: List[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("url") or value.get("contentUrl")
        if value and str(value) not in urls:
            urls.append(str(value))
    return urls


def _file_urls(urls: Iterable[str]) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    for index, url in enumerate(urls, start=1):
        filename = unquote(PurePosixPath(urlparse(url).path).name) or f"file-{index}"
        files.append({"url": url, "filename": filename})
    return files


def _source_name(payload: Dict[str, Any], attributes: Dict[str, Any]) -> str:
    client = ((payload.get("relationships") or {}).get("client") or {}).get("data") or {}
    client_name = ((client.get("attributes") or {}).get("name")) if isinstance(client, dict) else None
    publisher = attributes.get("publisher")
    if isinstance(publisher, dict):
        publisher = publisher.get("name")
    return str(client_name or publisher or "DataCite")


class DataCiteAdapter(ExternalRepoAdapter):
    """Resolve DOI metadata through DataCite's unauthenticated REST API."""

    def name(self) -> str:
        return "DataCite"

    def match(self, identifier: str) -> bool:
        return _DOI_RE.match(identifier.strip()) is not None

    def parse_identifier(self, identifier: str) -> str:
        match = _DOI_RE.match(identifier.strip())
        if not match:
            raise ValueError(f"Cannot parse DOI identifier: {identifier}")
        return match.group(1).rstrip("/")

    def fetch_metadata(self, identifier: str) -> Dict[str, Any]:
        doi = self.parse_identifier(identifier)
        endpoint = f"{DATACITE_API}/dois/{doi}"
        with httpx.Client(timeout=20.0) as client:
            response = client.get(endpoint)
            if response.status_code == 404:
                raise ExternalRecordNotFoundError(
                    f"DOI {doi} is not registered with DataCite. If it identifies a "
                    "Crossref publication, add it to related_works instead."
                )
            response.raise_for_status()
            payload = response.json().get("data") or {}

        attributes = payload.get("attributes") or {}
        url = str(attributes.get("url") or f"https://doi.org/{doi}")
        source = _source_name(payload, attributes)
        urls = _content_urls(attributes)
        abstract = ""
        descriptions = attributes.get("descriptions") or []
        for description in descriptions:
            if isinstance(description, dict) and str(description.get("descriptionType", "")).lower() == "abstract":
                abstract = str(description.get("description") or "")
                break
        if not abstract:
            abstract = _first_text(descriptions, "description")

        return {
            "title": _first_text(attributes.get("titles"), "title") or "Untitled",
            "description": abstract,
            "authors": _authors(attributes.get("creators")),
            "keywords": [
                str(subject["subject"] if isinstance(subject, dict) else subject)
                for subject in attributes.get("subjects") or []
                if (isinstance(subject, str) and subject) or (isinstance(subject, dict) and subject.get("subject"))
            ],
            "publication_year": attributes.get("publicationYear"),
            "license": _license(attributes.get("rightsList")),
            "related_works": _related_works(attributes.get("relatedIdentifiers")),
            "external_source": {"source": source, "doi": doi, "url": url},
            "external_identifier": doi,
            "doi": doi,
            "url": url,
            "data_sources": urls,
            "file_urls": _file_urls(urls),
        }
