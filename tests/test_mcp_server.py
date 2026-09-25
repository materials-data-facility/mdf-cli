"""Tests for the read-only MDF MCP server and the `mdf mcp` sub-app.

Everything here runs in-process against a fake ``BackendClient``: no network,
no MCP client, no stdio transport. The `mcp` SDK is only needed by the
``TestServerRegistration`` / ``TestInProcessCall`` classes, which skip without
it, so the tool layer stays testable on a bare install.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

from mdf.cli.mcp_cmd import CLIENTS, app as mcp_app, config_snippet
from mdf.core.backend_client import ApiResult
from mdf.mcp import server as mcp_server

runner = CliRunner()

EXPECTED_TOOLS = (
    "search_datasets",
    "get_dataset_card",
    "get_citation",
    "list_files",
    "get_sample",
    "get_versions",
    "resolve_doi",
)

SEARCH_RESPONSE = {
    "query": "perovskite",
    "total": 21,
    "offset": 0,
    "results": [
        {
            "type": "dataset",
            "source_id": "darpa_sd2_perovskites",
            "version": "1.0",
            "title": "Untangling How Machines Learn Perovskite Crystallization",
            "authors": ["Pendleton, Ian M.", "Schrier, Joshua"],
            "keywords": ["perovskite", "machine learning"],
            "description": "x" * 900,
            "publication_year": 2020,
            "organization": "MDF Open",
            "domains": [],
            "doi": None,
            "license": "CC-BY 4.0",
            "size_bytes": None,
            "file_count": None,
            "status": "published",
            "score": 0,
            "latest": True,
            "root_version": "darpa_sd2_perovskites",
            "download_url": "https://data.materialsdatafacility.org/mdf_open/darpa_sd2_perovskites_v1.2/",
        }
    ],
    "facets": {
        "Keywords": [{"value": f"k{i}", "count": i} for i in range(12)],
        "Domains": [],
    },
}

CARD_RESPONSE = {
    "success": True,
    "card": {
        "source_id": "foundry_wei_atom_locating_benchmark",
        "version": "1.1",
        "title": "Benchmark Dataset for Locating Atoms in STEM images",
        "authors": [{"name": "Wei, Jingrui"}],
        "keywords": ["STEM"],
    },
}

AGENT_CARD_RESPONSE = {
    "success": True,
    "card": {
        "source_id": "foundry_wei_atom_locating_benchmark",
        "version": "1.1",
        "title": "Benchmark Dataset for Locating Atoms in STEM images",
        "citation_apa": "Wei, J. (2021). Benchmark Dataset…",
        "loading_recipe": {"python": "from foundry_ml import Foundry"},
        "urls": {"landing": "https://materialsdatafacility.org/detail/x"},
    },
}

NOT_FOUND = ApiResult({"detail": "Submission not found"}, status_code=404)
NO_PROFILE = ApiResult({"detail": "No profile found for this dataset"}, status_code=404)


class FakeClient:
    """A stand-in BackendClient recording calls and replaying canned results."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    # -- the read methods the MCP tools use -------------------------------
    def _reply(self, name, **kwargs):
        self.calls.append((name, kwargs))
        value = self.responses.get(name, {"success": True})
        if isinstance(value, Exception):
            raise value
        return value

    def search(self, query=None, **kwargs):
        # ``_resolve_doi`` passes the query positionally; the MCP tool by name.
        return self._reply("search", query=query, **kwargs)

    def get_card(self, source_id, version=None):
        return self._reply("get_card", source_id=source_id, version=version)

    def get_citation(self, source_id, format="all", version=None):
        return self._reply("get_citation", source_id=source_id, format=format, version=version)

    def dataset_files(self, source_id):
        return self._reply("dataset_files", source_id=source_id)

    def dataset_sample(self, source_id):
        return self._reply("dataset_sample", source_id=source_id)

    def versions(self, source_id):
        return self._reply("versions", source_id=source_id)

    def _request(self, method, path, params=None):
        return self._reply("_request", method=method, path=path, params=params)

    def close(self):
        pass


@pytest.fixture
def fake_client(monkeypatch):
    """Install a FakeClient as the server's memoized client."""

    def _install(**responses):
        client = FakeClient(**responses)
        monkeypatch.setattr(mcp_server, "_CLIENT", client)
        return client

    return _install


class TestToolRegistry:
    """The tool set is the contract: 7 read-only tools, no write tools."""

    def test_tool_names_are_the_seven_read_tools(self):
        assert mcp_server.TOOL_NAMES == EXPECTED_TOOLS

    def test_every_spec_has_a_description_and_a_callable(self):
        for name, description, fn in mcp_server.TOOL_SPECS:
            assert callable(fn), name
            assert description and len(description) > 30, name
            assert fn.__doc__, name

    def test_no_write_verbs_in_the_tool_set(self):
        forbidden = ("publish", "submit", "approve", "reject", "withdraw", "delete", "edit")
        for name in mcp_server.TOOL_NAMES:
            assert not any(verb in name for verb in forbidden), name


class TestSearchDatasets:
    def test_returns_compact_records_and_trimmed_facets(self, fake_client):
        client = fake_client(search=SEARCH_RESPONSE)
        out = mcp_server.search_datasets(query="perovskite", limit=5)

        assert out["total"] == 21
        assert out["count"] == 1
        dataset = out["datasets"][0]
        assert dataset["source_id"] == "darpa_sd2_perovskites"
        # noisy internals dropped, useful fields kept
        assert "score" not in dataset and "root_version" not in dataset
        assert dataset["download_url"].endswith("darpa_sd2_perovskites_v1.2/")
        # long descriptions are truncated, not dumped into the context window
        assert len(dataset["description"]) <= mcp_server._MAX_SEARCH_DESCRIPTION
        # facets are capped and empty groups dropped
        assert len(out["facets"]["Keywords"]) == mcp_server._MAX_FACET_VALUES
        assert "Domains" not in out["facets"]
        assert client.calls[0][1]["search_type"] == "datasets"

    def test_filters_and_sort_are_forwarded(self, fake_client):
        client = fake_client(search=SEARCH_RESPONSE)
        mcp_server.search_datasets(
            query="alloy",
            year=["2021"],
            organization=["MDF Open"],
            author=["Ward, Logan"],
            keyword=["machine learning"],
            domain=["materials science"],
            sort="newest",
        )
        kwargs = client.calls[0][1]
        assert kwargs["year"] == ["2021"]
        assert kwargs["organization"] == ["MDF Open"]
        assert kwargs["author"] == ["Ward, Logan"]
        assert kwargs["keyword"] == ["machine learning"]
        assert kwargs["domain"] == ["materials science"]
        assert kwargs["sort"] == "newest"

    def test_browse_without_a_query(self, fake_client):
        client = fake_client(search=SEARCH_RESPONSE)
        out = mcp_server.search_datasets()
        assert out["query"] is None
        assert client.calls[0][1]["query"] is None

    def test_unknown_sort_is_a_validation_error(self, fake_client):
        client = fake_client(search=SEARCH_RESPONSE)
        out = mcp_server.search_datasets(query="x", sort="cheapest")
        assert out["error"]["kind"] == "validation"
        assert "cheapest" in out["error"]["message"]
        assert "newest" in out["error"]["hint"]
        assert client.calls == []  # rejected before any API call


class TestGetDatasetCard:
    def test_prefers_the_agent_format(self, fake_client):
        client = fake_client(_request=AGENT_CARD_RESPONSE)
        out = mcp_server.get_dataset_card("foundry_wei_atom_locating_benchmark")
        assert out["format"] == "agent"
        assert out["card"]["loading_recipe"]["python"].startswith("from foundry_ml")
        method, kwargs = client.calls[0]
        assert method == "_request"
        assert kwargs["params"]["format"] == "agent"

    def test_falls_back_to_the_normal_card(self, fake_client):
        client = fake_client(_request=NOT_FOUND, get_card=CARD_RESPONSE)
        out = mcp_server.get_dataset_card("foundry_wei_atom_locating_benchmark")
        assert out["format"] == "card"
        assert out["version"] == "1.1"
        assert out["card"]["title"].startswith("Benchmark Dataset")
        assert [name for name, _ in client.calls] == ["_request", "get_card"]

    def test_version_is_forwarded(self, fake_client):
        client = fake_client(_request=AGENT_CARD_RESPONSE)
        mcp_server.get_dataset_card("x", version="1.0")
        assert client.calls[0][1]["params"]["version"] == "1.0"

    def test_missing_dataset_returns_the_error_envelope(self, fake_client):
        fake_client(_request=NOT_FOUND, get_card=NOT_FOUND)
        out = mcp_server.get_dataset_card("no_such_dataset")
        assert set(("kind", "message", "hint")) <= set(out["error"])
        assert out["error"]["kind"] == "not_found"
        assert out["error"]["code"] == "HTTP 404"
        assert "card" not in out


class TestGetCitation:
    def test_returns_the_requested_format(self, fake_client):
        client = fake_client(
            get_citation={
                "success": True,
                "source_id": "abc",
                "version": "1.1",
                "bibtex": "@dataset{abc_2021, title = {{X}}}",
            }
        )
        out = mcp_server.get_citation("abc", format="bibtex")
        assert out["format"] == "bibtex"
        assert out["citation"].startswith("@dataset")
        assert out["version"] == "1.1"
        assert client.calls[0][1]["format"] == "bibtex"

    def test_defaults_to_apa(self, fake_client):
        client = fake_client(get_citation={"success": True, "apa": "Wei, J. (2021)."})
        out = mcp_server.get_citation("abc")
        assert out["format"] == "apa"
        assert out["citation"] == "Wei, J. (2021)."
        assert client.calls[0][1]["format"] == "apa"

    @pytest.mark.parametrize("bad", ["chicago", "datacite", "all"])
    def test_unsupported_format_is_rejected_locally(self, fake_client, bad):
        client = fake_client()
        out = mcp_server.get_citation("abc", format=bad)
        assert out["error"]["kind"] == "validation"
        assert client.calls == []


class TestFilesAndSample:
    def test_list_files_passes_through_and_caps(self, fake_client):
        files = [{"path": f"f{i}.csv", "size_bytes": i} for i in range(500)]
        fake_client(dataset_files={"success": True, "files": files})
        out = mcp_server.list_files("abc")
        assert len(out["files"]) == mcp_server._MAX_FILES
        assert out["files_truncated"] is True
        assert out["files_total"] == 500
        assert out["source_id"] == "abc"

    def test_missing_profile_says_so_instead_of_check_the_identifier(self, fake_client):
        fake_client(dataset_files=NO_PROFILE)
        out = mcp_server.list_files("foundry_wei_atom_locating_benchmark")
        assert out["error"]["kind"] == "not_found"
        assert "No file profile" in out["error"]["message"]
        assert "get_dataset_card" in out["error"]["hint"]

    def test_get_sample_caps_rows(self, fake_client):
        fake_client(
            dataset_sample={"success": True, "rows": [{"i": i} for i in range(100)]}
        )
        out = mcp_server.get_sample("abc")
        assert len(out["rows"]) == mcp_server._MAX_SAMPLE_ROWS
        assert out["rows_truncated"] is True
        assert out["rows_total"] == 100

    def test_get_sample_missing_profile(self, fake_client):
        fake_client(dataset_sample=NO_PROFILE)
        out = mcp_server.get_sample("abc")
        assert "No file profile" in out["error"]["message"]


class TestGetVersions:
    def test_returns_version_history(self, fake_client):
        fake_client(
            versions={
                "success": True,
                "source_id": "abc",
                "versions": [{"version": "1.0", "status": "published"}],
                "total_count": 1,
                "dataset_doi": "10.18126/e73h-3w6n",
            }
        )
        out = mcp_server.get_versions("abc")
        assert out["total_count"] == 1
        assert out["dataset_doi"] == "10.18126/e73h-3w6n"

    def test_unknown_dataset(self, fake_client):
        fake_client(versions=NOT_FOUND)
        out = mcp_server.get_versions("nope")
        assert out["error"]["kind"] == "not_found"


class TestResolveDoi:
    def test_resolves_a_doi_url_to_a_source_id(self, fake_client):
        fake_client(
            search={
                "success": True,
                "results": [{"doi": "10.18126/e73h-3w6n", "source_id": "wei_stem"}],
            },
            get_card={"success": True, "card": {"title": "STEM images", "version": "1.1"}},
        )
        out = mcp_server.resolve_doi("https://doi.org/10.18126/e73h-3w6n")
        assert out["doi"] == "10.18126/e73h-3w6n"
        assert out["source_id"] == "wei_stem"
        assert out["title"] == "STEM images"
        assert out["landing_url"].endswith("/detail/wei_stem")

    @pytest.mark.parametrize(
        "value", ["10.18126/e73h-3w6n", "doi:10.18126/e73h-3w6n"]
    )
    def test_accepts_bare_and_prefixed_dois(self, fake_client, value):
        fake_client(
            search={"success": True, "results": [{"doi": "10.18126/e73h-3w6n", "source_id": "wei_stem"}]},
            get_card=NOT_FOUND,
        )
        out = mcp_server.resolve_doi(value)
        assert out["source_id"] == "wei_stem"
        # A card that cannot be read does not fail the resolution
        assert "title" not in out

    def test_non_doi_input_is_a_validation_error(self, fake_client):
        client = fake_client()
        out = mcp_server.resolve_doi("perovskite datasets")
        assert out["error"]["kind"] == "validation"
        assert client.calls == []

    def test_unhosted_doi_is_not_found(self, fake_client):
        fake_client(search={"success": True, "results": []}, get_card=NOT_FOUND)
        out = mcp_server.resolve_doi("10.5281/zenodo.12345")
        assert out["error"]["kind"] == "not_found"
        assert out["error"]["doi"] == "10.5281/zenodo.12345"
        assert "search_datasets" in out["error"]["hint"]


class TestErrorHandling:
    def test_tools_never_raise(self, fake_client):
        fake_client(search=RuntimeError("socket exploded"))
        out = mcp_server.search_datasets(query="x")
        assert out["error"]["kind"] == "unknown"
        assert "socket exploded" in out["error"]["message"]

    def test_network_failure_is_classified(self, fake_client):
        fake_client(versions=ApiResult({"success": False, "error": "Connection failed after 4 attempts"}))
        out = mcp_server.get_versions("abc")
        assert out["error"]["kind"] == "network"
        assert out["error"]["hint"]

    def test_error_payloads_always_carry_the_three_keys(self, fake_client):
        fake_client(get_citation=ApiResult({"detail": "nope"}, status_code=403))
        out = mcp_server.get_citation("abc")
        assert set(("kind", "message", "hint")) <= set(out["error"])
        assert out["error"]["kind"] == "forbidden"


class TestServiceSelection:
    def test_explicit_api_url_wins(self, monkeypatch):
        monkeypatch.delenv("MDF_API_URL", raising=False)
        mcp_server.configure(api_url="http://127.0.0.1:9999")
        try:
            assert mcp_server.base_url() == "http://127.0.0.1:9999"
        finally:
            mcp_server.configure()

    def test_service_selects_the_api_url(self, monkeypatch):
        monkeypatch.delenv("MDF_API_URL", raising=False)
        mcp_server.configure(service="local")
        try:
            assert mcp_server.base_url() == "http://127.0.0.1:8080"
        finally:
            mcp_server.configure()

    def test_env_service_is_honored(self, monkeypatch):
        monkeypatch.delenv("MDF_API_URL", raising=False)
        monkeypatch.setenv("MDF_SERVICE", "local")
        mcp_server.configure()
        try:
            assert mcp_server.base_url() == "http://127.0.0.1:8080"
        finally:
            mcp_server.configure()

    def test_client_is_anonymous(self, monkeypatch):
        """The MCP server must never carry credentials or trigger a login."""
        monkeypatch.delenv("MDF_API_URL", raising=False)
        monkeypatch.setenv("MDF_SERVICE", "staging")
        mcp_server.configure()
        try:
            client = mcp_server._get_client()
            assert client._token is None
            assert client._groups_token is None
            assert client._user_id is None
        finally:
            mcp_server.configure()


class TestServerRegistration:
    """The MCP SDK layer: needs the `mcp` extra."""

    @pytest.fixture(autouse=True)
    def _requires_mcp(self):
        pytest.importorskip("mcp", reason='needs the MCP extra: pip install "mdf-cli[mcp]"')

    def test_create_server_registers_all_seven_tools(self):
        server = mcp_server.create_server()
        tools = asyncio.run(server.list_tools())
        assert tuple(tool.name for tool in tools) == EXPECTED_TOOLS
        for tool in tools:
            assert tool.description

    def test_tool_schemas_expose_the_documented_arguments(self):
        server = mcp_server.create_server()
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

        def schema(tool):
            return getattr(tool, "input_schema", None) or tool.inputSchema

        search = schema(tools["search_datasets"])
        assert set(search["properties"]) == {
            "query", "limit", "year", "organization", "author", "keyword", "domain", "sort",
        }
        assert "query" not in search.get("required", [])
        card = schema(tools["get_dataset_card"])
        assert card["required"] == ["source_id"]


class TestInProcessCall:
    """Round-trip one tool through the SDK's own dispatch."""

    @pytest.fixture(autouse=True)
    def _requires_mcp(self):
        pytest.importorskip("mcp", reason='needs the MCP extra: pip install "mdf-cli[mcp]"')

    @staticmethod
    def _payload(result):
        structured = getattr(result, "structured_content", None) or getattr(
            result, "structuredContent", None
        )
        if isinstance(structured, dict):
            # mcp wraps a bare dict return under "result".
            return structured.get("result", structured)
        return json.loads(result.content[0].text)

    def test_call_tool_returns_the_tool_payload(self, fake_client):
        fake_client(search=SEARCH_RESPONSE)
        server = mcp_server.create_server()
        result = asyncio.run(server.call_tool("search_datasets", {"query": "perovskite", "limit": 5}))
        payload = self._payload(result)
        assert payload["count"] == 1
        assert payload["datasets"][0]["source_id"] == "darpa_sd2_perovskites"

    def test_call_tool_surfaces_the_error_envelope(self, fake_client):
        fake_client(_request=NOT_FOUND, get_card=NOT_FOUND)
        server = mcp_server.create_server()
        result = asyncio.run(server.call_tool("get_dataset_card", {"source_id": "nope"}))
        payload = self._payload(result)
        assert payload["error"]["kind"] == "not_found"


class TestMcpCliInstall:
    def test_prints_valid_json_for_every_client(self, monkeypatch):
        monkeypatch.setenv("MDF_SERVICE", "staging")
        for client in CLIENTS:
            result = runner.invoke(mcp_app, ["install", "--client", client])
            assert result.exit_code == 0, client
            snippet = json.loads(result.stdout)
            server = snippet["mcpServers"]["mdf"]
            assert server["args"][-2:] == ["mcp", "serve"]
            assert server["command"]
            # No --service: nothing pinned, so the server follows the CLI
            # default (and follows it when the default flips to prod).
            assert "MDF_SERVICE" not in server.get("env", {})

    def test_service_flag_is_pinned_in_the_snippet(self, monkeypatch):
        monkeypatch.setenv("MDF_SERVICE", "staging")
        result = runner.invoke(mcp_app, ["install", "--service", "prod"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["mcpServers"]["mdf"]["env"] == {"MDF_SERVICE": "prod"}

    def test_api_url_flag_replaces_the_service(self):
        result = runner.invoke(mcp_app, ["install", "--api-url", "http://127.0.0.1:8080"])
        assert result.exit_code == 0
        env = json.loads(result.stdout)["mcpServers"]["mdf"]["env"]
        assert env == {"MDF_API_URL": "http://127.0.0.1:8080"}

    def test_unknown_client_is_a_usage_error(self):
        result = runner.invoke(mcp_app, ["install", "--client", "emacs"])
        assert result.exit_code == 2

    def test_command_is_an_absolute_path_or_python_module(self, monkeypatch):
        monkeypatch.setenv("MDF_SERVICE", "staging")
        snippet = config_snippet()["mcpServers"]["mdf"]
        assert snippet["command"].startswith("/") or snippet["command"][1:3] == ":\\"
        assert snippet["args"][-2:] == ["mcp", "serve"]

    def test_install_writes_nothing(self, monkeypatch, tmp_path):
        """`install` prints; it never touches a client's config file."""
        monkeypatch.setenv("MDF_SERVICE", "staging")
        monkeypatch.setenv("HOME", str(tmp_path))
        before = set(tmp_path.rglob("*"))
        assert runner.invoke(mcp_app, ["install", "--client", "cursor"]).exit_code == 0
        assert set(tmp_path.rglob("*")) == before


class TestMcpCliServe:
    def test_help_documents_the_read_only_tools(self):
        result = runner.invoke(mcp_app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "read-only" in result.stdout
        assert "search_datasets" in result.stdout

    def test_serve_reports_a_missing_mcp_extra(self, monkeypatch):
        def _no_sdk(*args, **kwargs):
            raise ImportError(mcp_server.MCP_INSTALL_HINT)

        monkeypatch.setattr(mcp_server, "run_server", _no_sdk)
        result = runner.invoke(mcp_app, ["serve"])
        assert result.exit_code == 1
        assert "mdf-cli[mcp]" in (result.stderr or "") + result.stdout

    def test_serve_forwards_service_selection(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            mcp_server, "run_server", lambda **kwargs: seen.update(kwargs)
        )
        result = runner.invoke(mcp_app, ["serve", "--service", "prod"])
        assert result.exit_code == 0
        assert seen == {"service": "prod", "api_url": None}
