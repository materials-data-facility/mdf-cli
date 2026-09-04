from __future__ import annotations

import httpx
import pytest

from mdf.importers.base import ExternalRecordNotFoundError
from mdf.importers.datacite import DataCiteAdapter
from mdf.importers.registry import resolve_adapter
from mdf.importers.zenodo import ZenodoAdapter


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client(*args, **kwargs):
        assert kwargs["timeout"] == 20.0
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)


def test_datacite_maps_manifest_metadata(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://api.datacite.org/dois/10.1234/example"
        return httpx.Response(
            200,
            json={
                "data": {
                    "relationships": {
                        "client": {"data": {"attributes": {"name": "Example Repository"}}}
                    },
                    "attributes": {
                        "titles": [{"title": "A useful dataset"}],
                        "descriptions": [
                            {"descriptionType": "Methods", "description": "not this"},
                            {"descriptionType": "Abstract", "description": "The abstract."},
                        ],
                        "creators": [{
                            "name": "Lovelace, Ada",
                            "givenName": "Ada",
                            "familyName": "Lovelace",
                            "affiliation": [{"name": "Analytical Engine Institute"}],
                            "nameIdentifiers": [{
                                "nameIdentifierScheme": "ORCID",
                                "nameIdentifier": "https://orcid.org/0000-0001-2345-6789",
                            }],
                        }],
                        "subjects": [{"subject": "materials"}, {"subject": "benchmark"}],
                        "publicationYear": 2024,
                        "rightsList": [{
                            "rights": "Creative Commons Attribution 4.0",
                            "rightsIdentifier": "CC-BY-4.0",
                            "rightsUri": "https://creativecommons.org/licenses/by/4.0/",
                        }],
                        "relatedIdentifiers": [{
                            "relatedIdentifier": "10.1000/article",
                            "relatedIdentifierType": "DOI",
                            "relationType": "IsSupplementTo",
                            "description": "Companion paper",
                        }],
                        "publisher": "Fallback Publisher",
                        "url": "https://repository.example/record/42",
                        "contentUrl": ["https://repository.example/files/data.csv"],
                        "media": [{"url": "https://repository.example/files/readme.txt"}],
                    },
                }
            },
        )

    _mock_client(monkeypatch, handler)
    metadata = DataCiteAdapter().fetch_metadata("doi:10.1234/example")

    assert metadata["title"] == "A useful dataset"
    assert metadata["description"] == "The abstract."
    assert metadata["authors"] == [{
        "name": "Lovelace, Ada",
        "given_name": "Ada",
        "family_name": "Lovelace",
        "affiliations": ["Analytical Engine Institute"],
        "orcid": "0000-0001-2345-6789",
    }]
    assert metadata["keywords"] == ["materials", "benchmark"]
    assert metadata["publication_year"] == 2024
    assert metadata["license"]["identifier"] == "CC-BY-4.0"
    assert metadata["related_works"] == [{
        "identifier": "10.1000/article",
        "identifier_type": "DOI",
        "relation_type": "IsSupplementTo",
        "description": "Companion paper",
    }]
    assert metadata["external_source"] == {
        "source": "Example Repository",
        "doi": "10.1234/example",
        "url": "https://repository.example/record/42",
    }
    assert metadata["data_sources"] == [
        "https://repository.example/files/data.csv",
        "https://repository.example/files/readme.txt",
    ]
    assert [item["filename"] for item in metadata["file_urls"]] == ["data.csv", "readme.txt"]


@pytest.mark.parametrize(
    "identifier",
    ["10.1234/example", "doi:10.1234/example", "https://doi.org/10.1234/example"],
)
def test_datacite_accepts_all_doi_spellings(identifier: str):
    adapter = DataCiteAdapter()
    assert adapter.match(identifier)
    assert adapter.parse_identifier(identifier) == "10.1234/example"


def test_zenodo_doi_routes_to_zenodo_adapter():
    adapter = resolve_adapter("https://doi.org/10.5281/zenodo.12345")
    assert isinstance(adapter, ZenodoAdapter)
    assert adapter.parse_identifier("https://doi.org/10.5281/zenodo.12345") == "12345"


def test_crossref_only_doi_has_actionable_error(monkeypatch: pytest.MonkeyPatch):
    _mock_client(monkeypatch, lambda request: httpx.Response(404, json={"errors": []}))
    with pytest.raises(ExternalRecordNotFoundError, match="related_works"):
        DataCiteAdapter().fetch_metadata("10.5555/crossref-only")
