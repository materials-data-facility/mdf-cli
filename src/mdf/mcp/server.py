"""MDF MCP server — read-only Materials Data Facility tools for AI assistants.

A thin Model Context Protocol layer over the **public** MDF v2 REST API, so any
MCP-capable assistant (Claude Code, Claude Desktop, Cursor, lab agents) can
discover, inspect and cite MDF datasets without a browser.

Tools provided (all read-only):

- ``search_datasets``   — keyword/browse search with facet filters
- ``get_dataset_card``  — one dataset's metadata card (agent format when available)
- ``get_citation``      — APA / BibTeX / RIS citation
- ``list_files``        — the dataset's file listing
- ``get_sample``        — a small data sample (tabular head, structure)
- ``get_versions``      — version history
- ``resolve_doi``       — DOI → MDF source_id

Design notes:

- **Read-only by construction.** Every tool is a GET against the public API via
  an *anonymous* :class:`~mdf.core.backend_client.BackendClient`, so the server
  never authenticates, never prompts for a login, and cannot publish, edit,
  approve or withdraw anything. Write tools are deliberately out of scope for
  this MVP (see Design 4 in the LLM roadmap).
- **No network or config I/O at start-up.** The client is built on the first
  tool call, so launching the server is instant and offline-safe.
- **stdout carries the MCP protocol and nothing else.** Rich consoles reached
  through :mod:`mdf.cli.formatting` (and any stray library ``print``) are kept
  off the protocol stream by a server-lifetime stdout guard; logs go to stderr.
- **Tools never raise.** Expected failures (unknown source_id, dataset without a
  profile, network trouble) come back as ``{"error": {"kind", "message",
  "hint"}}`` classified by the CLI's own
  :func:`~mdf.cli.formatting.classify_api_error`, so an agent sees the same
  error vocabulary the CLI prints.
- Import-safe without the ``mcp`` package: the tool functions and
  :data:`TOOL_SPECS` can be imported (and tested) with no MCP SDK installed;
  only :func:`create_server` needs it.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import sys
import threading
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

logger = logging.getLogger(__name__)

SERVER_NAME = "mdf"

SERVER_INSTRUCTIONS = (
    "The Materials Data Facility (MDF) hosts published, citable materials science "
    "datasets. Use search_datasets to find datasets (omit the query to browse the "
    "newest ones; the returned facets show the exact values accepted by the year, "
    "organization, author, keyword and domain filters), then get_dataset_card for "
    "full metadata, list_files/get_sample to see what is inside, get_versions for "
    "version history, and get_citation when the user needs to cite it. resolve_doi "
    "turns a DOI into the MDF source_id the other tools take. These tools are "
    "read-only: publishing a dataset is done by the researcher with the `mdf` CLI "
    "(`mdf publish`), never by this server."
)

MCP_INSTALL_HINT = (
    "The MDF MCP server requires additional dependencies. "
    'Install with: pip install "mdf-cli[mcp]"'
)

#: Descriptions longer than this are truncated in search results (an agent that
#: wants the full abstract calls ``get_dataset_card``).
_MAX_SEARCH_DESCRIPTION = 400
#: Facet values reported per facet group in a search result.
_MAX_FACET_VALUES = 5
#: Caps on list-shaped payloads, so one tool call cannot flood a context window.
_MAX_FILES = 200
_MAX_SAMPLE_ROWS = 20

CITATION_FORMATS = ("apa", "bibtex", "ris")


# ----------------------------------------------------------------------
# stdout hygiene (stdio transport)
# ----------------------------------------------------------------------

_stdout_guard_lock = threading.Lock()
_stdout_guard_depth = 0
_stdout_guard_saved: Any = None


@contextlib.contextmanager
def _stdout_guard():
    """Alias ``sys.stdout`` to ``sys.stderr`` for the server's whole lifetime.

    On a stdio MCP server anything written to stdout corrupts the protocol
    stream, and this process imports Rich consoles (via
    :mod:`mdf.cli.formatting`) that are bound to stdout.

    Deliberately **not** per tool call: ``sys.stdout`` is process-global, so a
    per-call swap is unsafe under concurrency. Refcounted so nesting is
    harmless — only the outermost exit restores the original stream.
    """
    global _stdout_guard_depth, _stdout_guard_saved

    with _stdout_guard_lock:
        if _stdout_guard_depth == 0:
            _stdout_guard_saved = sys.stdout
            sys.stdout = sys.stderr
        _stdout_guard_depth += 1
    try:
        yield
    finally:
        with _stdout_guard_lock:
            _stdout_guard_depth -= 1
            if _stdout_guard_depth == 0:
                sys.stdout = _stdout_guard_saved
                _stdout_guard_saved = None


@contextlib.asynccontextmanager
async def _server_lifespan(server):
    """Server-lifetime setup: reserve stdout for the MCP protocol.

    The SDK enters this once the transport has captured the real stdout and
    exits it at shutdown, so the redirection spans every tool call without any
    per-call mutation of process-global state.
    """
    with _stdout_guard():
        logger.info("MDF MCP server ready; stdout is reserved for the MCP protocol")
        yield {}


# ----------------------------------------------------------------------
# Service selection + lazy anonymous client
# ----------------------------------------------------------------------

_SETTINGS: Dict[str, Optional[str]] = {"service": None, "api_url": None}
_CLIENT = None


def configure(service: Optional[str] = None, api_url: Optional[str] = None) -> None:
    """Pin the service/API URL this server reads from (called by ``mdf mcp serve``).

    ``None`` leaves the value unset, in which case service selection falls back
    to the CLI's own precedence: ``MDF_API_URL`` > ``MDF_SERVICE`` > the config
    file's ``defaults.service`` > the release default.
    """
    _SETTINGS["service"] = service
    _SETTINGS["api_url"] = api_url
    _reset_client()


def _reset_client() -> None:
    """Drop the memoized client (used by :func:`configure` and by tests)."""
    global _CLIENT
    if _CLIENT is not None:
        with contextlib.suppress(Exception):
            _CLIENT.close()
    _CLIENT = None


def base_url() -> str:
    """The API base URL this server reads from."""
    from mdf.core.backend_client import _api_url_for_service

    # ``_api_url_for_service`` already honors MDF_API_URL and MDF_SERVICE.
    return _SETTINGS["api_url"] or _api_url_for_service(_SETTINGS["service"])


def _get_client():
    """Return a memoized **anonymous** BackendClient.

    Anonymous on purpose: this server only reads public endpoints, so it must
    never trigger an interactive Globus login or carry a user's credentials.
    """
    global _CLIENT
    if _CLIENT is None:
        from mdf.core.backend_client import BackendClient

        _CLIENT = BackendClient(base_url=base_url())
    return _CLIENT


# ----------------------------------------------------------------------
# Result / error helpers
# ----------------------------------------------------------------------

def _error(kind: str, message: str, hint: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    """Build the tool error payload: ``{"error": {"kind", "message", "hint"}}``."""
    payload: Dict[str, Any] = {"kind": kind, "message": message, "hint": hint}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return {"error": payload}


def _api_error(result: Any) -> Dict[str, Any]:
    """Classify a failed API result with the CLI's own error vocabulary."""
    from mdf.cli.formatting import classify_api_error

    info = classify_api_error(result)
    return _error(
        str(info["kind"]),
        str(info["message"]),
        info["hint"],
        code=info["code"],
        detail=info["detail"],
    )


_PROFILE_MISSING_HINT = (
    "this dataset has no file profile yet — call get_dataset_card for its "
    "data_sources and download_url instead"
)


def _preview_error(result: Any, source_id: str) -> Dict[str, Any]:
    """Classify a ``/preview`` failure, naming the common "no profile yet" case.

    Most migrated MDF records have no file profile, and the bare ``not_found``
    message ("check the identifier") sends an agent chasing a typo that is not
    there. The raw server text is what distinguishes the two.
    """
    payload = _api_error(result)
    error = payload["error"]
    raw = ""
    if isinstance(result, dict):
        raw = str(result.get("detail") or result.get("error") or "")
    if error["kind"] == "not_found" and "profile" in raw.lower():
        error["message"] = f"No file profile for '{source_id}'"
        error["hint"] = _PROFILE_MISSING_HINT
        error["detail"] = raw
    return payload


def _is_success(result: Any) -> bool:
    from mdf.cli.formatting import is_success

    return is_success(result)


def _tool(func):
    """Wrap a tool: structured error payloads instead of exceptions/tracebacks."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # never let a traceback reach the wire
            logger.exception("MCP tool %s raised an unexpected error", func.__name__)
            return _error(
                "unknown",
                f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
                "retry, or run the same query with the `mdf` CLI to see the raw response",
            )

    return wrapper


def _truncate(text: Any, limit: int) -> Optional[str]:
    if not text:
        return text if text is None else ""
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _compact_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Trim one search hit to the fields an agent needs to triage it."""
    keep = (
        "type",
        "source_id",
        "version",
        "title",
        "authors",
        "publication_year",
        "organization",
        "doi",
        "license",
        "keywords",
        "domains",
        "size_bytes",
        "file_count",
        "status",
        "download_url",
    )
    compact = {key: hit[key] for key in keep if key in hit}
    if "description" in hit:
        compact["description"] = _truncate(hit.get("description"), _MAX_SEARCH_DESCRIPTION)
    return compact


def _compact_facets(facets: Any) -> Dict[str, Any]:
    """Top facet values per group — the vocabulary the filter args accept."""
    if not isinstance(facets, dict):
        return {}
    trimmed: Dict[str, Any] = {}
    for name, values in facets.items():
        if isinstance(values, list) and values:
            trimmed[name] = values[:_MAX_FACET_VALUES]
    return trimmed


def _cap_list(payload: Dict[str, Any], key: str, limit: int) -> Dict[str, Any]:
    """Return ``payload`` with ``payload[key]`` capped at ``limit`` entries."""
    values = payload.get(key)
    if isinstance(values, list) and len(values) > limit:
        capped = dict(payload)
        capped[key] = values[:limit]
        capped[f"{key}_truncated"] = True
        capped[f"{key}_total"] = len(values)
        return capped
    return dict(payload)


# ----------------------------------------------------------------------
# Tool argument annotations
# ----------------------------------------------------------------------

_QUERY = Field(
    description=(
        "Search terms, e.g. 'perovskite band gap'. Omit to browse the newest "
        "published datasets."
    )
)
_LIMIT = Field(description="Maximum number of datasets to return (backend max 50)", ge=1, le=50)
_SOURCE_ID = Field(
    description="The MDF source_id of the dataset, e.g. 'foundry_wei_atom_locating_benchmark'"
)
_VERSION = Field(description="Dataset version, e.g. '1.1'. Omit for the latest version.")
_FILTER_HINT = (
    "Filter by exact {name} facet value (as returned in a search result's `facets`); "
    "pass a list to OR several values."
)


def _filter_field(name: str, example: str) -> Any:
    return Field(description=_FILTER_HINT.format(name=name) + f" Example: {example!r}.")


# ----------------------------------------------------------------------
# Tools (read-only)
# ----------------------------------------------------------------------

@_tool
def search_datasets(
    query: Annotated[Optional[str], _QUERY] = None,
    limit: Annotated[int, _LIMIT] = 10,
    year: Annotated[Optional[List[str]], _filter_field("publication year", "2021")] = None,
    organization: Annotated[Optional[List[str]], _filter_field("organization", "MDF Open")] = None,
    author: Annotated[Optional[List[str]], _filter_field("author", "Ward, Logan")] = None,
    keyword: Annotated[Optional[List[str]], _filter_field("keyword", "machine learning")] = None,
    domain: Annotated[Optional[List[str]], _filter_field("scientific domain", "materials science")] = None,
    sort: Annotated[
        Optional[str],
        Field(description="Result order: 'relevance' (default), 'newest', or 'most_viewed'"),
    ] = None,
) -> Dict[str, Any]:
    """Search published MDF datasets by keyword, with optional facet filters.

    Omit ``query`` to browse the newest datasets. Filters take whole facet
    values, not fragments — read them off a previous result's ``facets``.

    Returns:
        ``{"query", "total", "count", "datasets": [...], "facets": {...}}``, or
        an ``{"error": ...}`` payload.
    """
    from mdf.core.backend_client import BackendClient

    if sort and sort not in BackendClient.SEARCH_SORTS:
        return _error(
            "validation",
            f"Unknown sort '{sort}'",
            f"use one of: {', '.join(BackendClient.SEARCH_SORTS)}",
        )

    result = _get_client().search(
        query=query,
        search_type="datasets",
        limit=limit,
        sort=sort,
        year=year,
        organization=organization,
        author=author,
        keyword=keyword,
        domain=domain,
    )
    if not _is_success(result):
        return _api_error(result)

    hits = [_compact_hit(hit) for hit in result.get("results", []) if isinstance(hit, dict)]
    return {
        "query": query,
        "total": result.get("total", len(hits)),
        "count": len(hits),
        "datasets": hits,
        "facets": _compact_facets(result.get("facets")),
    }


def _card_payload(source_id: str, version: Optional[str]) -> Any:
    """Fetch a card, preferring the compact agent format when the API serves it.

    ``BackendClient.get_card`` cannot pass ``format``, so the agent variant is
    requested directly; a backend that does not know the parameter simply
    returns the normal card (and one that rejects it falls back below).
    """
    client = _get_client()
    params: Dict[str, Any] = {"format": "agent"}
    if version:
        params["version"] = version
    try:
        result = client._request("GET", f"/card/{source_id}", params=params)
    except Exception:  # pragma: no cover - transport errors are already ApiResults
        result = None
    if result is not None and _is_success(result):
        return result
    return client.get_card(source_id, version=version)


@_tool
def get_dataset_card(
    source_id: Annotated[str, _SOURCE_ID],
    version: Annotated[Optional[str], _VERSION] = None,
) -> Dict[str, Any]:
    """Get one dataset's full metadata card.

    Prefers the API's compact ``format=agent`` card (schema/columns, loading
    recipe, citation, link health) and falls back to the standard card.

    Returns:
        ``{"source_id", "version", "format", "card": {...}}`` or ``{"error": ...}``.
    """
    result = _card_payload(source_id, version)
    if not _is_success(result):
        return _api_error(result)

    card = result.get("card") if isinstance(result.get("card"), dict) else result
    # The agent card is identifiable by its own fields; a backend that ignored
    # `format=agent` returns the standard card and is reported as such.
    is_agent = any(key in card for key in ("loading_recipe", "citation_apa", "urls"))
    return {
        "source_id": card.get("source_id", source_id),
        "version": card.get("version", version),
        "format": "agent" if is_agent else "card",
        "card": card,
    }


@_tool
def get_citation(
    source_id: Annotated[str, _SOURCE_ID],
    format: Annotated[
        str, Field(description="Citation format: 'apa', 'bibtex' or 'ris'")
    ] = "apa",
    version: Annotated[Optional[str], _VERSION] = None,
) -> Dict[str, Any]:
    """Get a formatted citation for a dataset.

    Returns:
        ``{"source_id", "version", "format", "citation"}`` or ``{"error": ...}``.
    """
    requested = (format or "apa").lower()
    if requested not in CITATION_FORMATS:
        return _error(
            "validation",
            f"Unknown citation format '{format}'",
            f"use one of: {', '.join(CITATION_FORMATS)}",
        )

    result = _get_client().get_citation(source_id, format=requested, version=version)
    if not _is_success(result):
        return _api_error(result)
    return {
        "source_id": result.get("source_id", source_id),
        "version": result.get("version", version),
        "format": requested,
        "citation": result.get(requested),
    }


@_tool
def list_files(source_id: Annotated[str, _SOURCE_ID]) -> Dict[str, Any]:
    """List the files in a dataset (name, size, format), when a profile exists.

    Many migrated MDF records have no file profile yet; those return a
    ``not_found`` error rather than an empty listing.

    Returns:
        the file listing (capped at 200 entries) or ``{"error": ...}``.
    """
    result = _get_client().dataset_files(source_id)
    if not _is_success(result):
        return _preview_error(result, source_id)
    payload = _cap_list(result, "files", _MAX_FILES)
    payload.setdefault("source_id", source_id)
    return payload


@_tool
def get_sample(source_id: Annotated[str, _SOURCE_ID]) -> Dict[str, Any]:
    """Get a small sample of a dataset's contents (tabular head or structure).

    Use this to check the shape of the data before recommending a download.

    Returns:
        the sample payload (row lists capped at 20) or ``{"error": ...}``.
    """
    result = _get_client().dataset_sample(source_id)
    if not _is_success(result):
        return _preview_error(result, source_id)
    payload = dict(result)
    for key in ("rows", "sample", "records"):
        payload = _cap_list(payload, key, _MAX_SAMPLE_ROWS)
    payload.setdefault("source_id", source_id)
    return payload


@_tool
def get_versions(source_id: Annotated[str, _SOURCE_ID]) -> Dict[str, Any]:
    """Get a dataset's version history (version, status, DOI, dates).

    Returns:
        ``{"source_id", "versions": [...], "total_count", "dataset_doi"}`` or
        ``{"error": ...}``.
    """
    result = _get_client().versions(source_id)
    if not _is_success(result):
        return _api_error(result)
    payload = dict(result)
    payload.setdefault("source_id", source_id)
    return payload


@_tool
def resolve_doi(
    doi: Annotated[
        str,
        Field(
            description=(
                "A DOI, bare ('10.18126/e73h-3w6n'), prefixed ('doi:10.18126/…') "
                "or as a URL ('https://doi.org/10.18126/…')"
            )
        ),
    ]
) -> Dict[str, Any]:
    """Resolve a DOI to the MDF source_id the other tools take.

    Returns:
        ``{"doi", "source_id", "title", "version", "landing_url"}`` or
        ``{"error": ...}`` when the input is not a DOI or MDF does not host it.
    """
    # Reuse the CLI's DOI parsing/resolution rather than re-deriving it.
    from mdf.core.agent import _extract_doi, _resolve_doi

    normalized = _extract_doi(doi or "")
    if not normalized:
        return _error(
            "validation",
            f"'{doi}' does not look like a DOI",
            "pass a DOI like 10.18126/e73h-3w6n, doi:10.…, or https://doi.org/10.…",
        )

    client = _get_client()
    source_id = _resolve_doi(client, normalized)
    if not source_id:
        return _error(
            "not_found",
            f"No MDF dataset found for DOI {normalized}",
            "MDF may not host this DOI — try search_datasets with the title instead",
            doi=normalized,
        )

    payload: Dict[str, Any] = {
        "doi": normalized,
        "source_id": source_id,
        "landing_url": f"https://materialsdatafacility.org/detail/{source_id}",
    }
    card = client.get_card(source_id)
    if _is_success(card):
        details = card.get("card") if isinstance(card.get("card"), dict) else card
        payload["title"] = details.get("title")
        payload["version"] = details.get("version")
    return payload


# (name, description, implementation) — the registry the MCP server is built
# from. Import-safe (no `mcp` dependency) so the tool set can be inspected and
# tested without the extra installed.
TOOL_SPECS = (
    (
        "search_datasets",
        "Search published Materials Data Facility datasets by keyword, with optional "
        "year/organization/author/keyword/domain filters. Omit the query to browse the "
        "newest datasets. Returns compact records plus the facet values accepted by the "
        "filters.",
        search_datasets,
    ),
    (
        "get_dataset_card",
        "Get one MDF dataset's full metadata card: title, authors and affiliations, "
        "description, keywords, license, DOI, size and file counts, data sources and "
        "download URL. Use after search_datasets to inspect a candidate.",
        get_dataset_card,
    ),
    (
        "get_citation",
        "Get a formatted citation (APA, BibTeX or RIS) for an MDF dataset. Use this "
        "whenever the user needs to cite data they found here.",
        get_citation,
    ),
    (
        "list_files",
        "List the files in an MDF dataset with names, sizes and formats. Not every "
        "dataset has a file profile yet.",
        list_files,
    ),
    (
        "get_sample",
        "Get a small sample of an MDF dataset's contents (e.g. the head of a tabular "
        "file) to check its shape before downloading.",
        get_sample,
    ),
    (
        "get_versions",
        "Get an MDF dataset's version history, with each version's status, DOI and dates.",
        get_versions,
    ),
    (
        "resolve_doi",
        "Resolve a DOI to the MDF source_id used by the other tools. Accepts a bare DOI, "
        "a doi: prefix, or a doi.org URL.",
        resolve_doi,
    ),
)

TOOL_NAMES = tuple(name for name, _description, _fn in TOOL_SPECS)

TOOLS = {name: fn for name, _description, fn in TOOL_SPECS}


# ----------------------------------------------------------------------
# Server construction (mcp 1.x FastMCP / mcp 2.x MCPServer)
# ----------------------------------------------------------------------

def _server_class():
    """Return the MCP SDK server class, or raise a helpful ImportError.

    ``MCPServer`` is the mcp 2.x name for what mcp 1.x called ``FastMCP``; both
    expose the ``.tool()`` decorator and ``.run(transport="stdio")`` used here.
    """
    try:
        from mcp.server import MCPServer  # mcp >= 2.0

        return MCPServer
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x

        return FastMCP
    except ImportError as exc:
        raise ImportError(MCP_INSTALL_HINT) from exc


def _client_version() -> str:
    """The installed mdf-cli version, reported to MCP clients."""
    try:
        from mdf.version import __version__

        return __version__
    except Exception:  # pragma: no cover
        return ""


def _constructor_attempts():
    """Constructor kwargs to try, richest first.

    mcp 2.x ``MCPServer`` takes all of these. mcp 1.x ``FastMCP(name,
    instructions=..., **settings)`` has no ``version`` kwarg and rejects it, so
    each rung drops exactly one capability rather than falling all the way back
    to a bare name.
    """
    named = {
        "name": SERVER_NAME,
        "instructions": SERVER_INSTRUCTIONS,
        "lifespan": _server_lifespan,
    }
    return (
        dict(named, version=_client_version()),
        named,                                                       # mcp 1.x: no `version`
        {"name": SERVER_NAME, "instructions": SERVER_INSTRUCTIONS},  # no `lifespan` support
        {"name": SERVER_NAME},                                       # no `instructions` support
    )


def _build_server(server_cls):
    """Instantiate ``server_cls`` with the richest kwargs it accepts."""
    attempts = _constructor_attempts()
    for index, kwargs in enumerate(attempts):
        try:
            return server_cls(**kwargs)
        except (TypeError, PydanticValidationError) as exc:
            if index == len(attempts) - 1:
                raise
            dropped = sorted(set(kwargs) - set(attempts[index + 1]))
            if "lifespan" in dropped:
                logger.warning(
                    "This `mcp` release does not support a server lifespan, so stdout cannot "
                    "be reserved for the MCP protocol; upgrade `mcp` if library output "
                    "corrupts the session (%s)",
                    exc,
                )
            elif "instructions" in dropped:
                logger.warning(
                    "This `mcp` release does not accept server instructions; clients will "
                    "see the tool list without them (%s)",
                    exc,
                )
            else:
                logger.debug(
                    "%s does not accept %s; retrying without it (%s)",
                    getattr(server_cls, "__name__", server_cls),
                    dropped,
                    exc,
                )


def create_server():
    """Build the MDF MCP server with all read-only tools registered.

    Returns:
        An MCP SDK server instance (``mcp.server.MCPServer``, or ``FastMCP`` on
        mcp 1.x) that has not been started yet.

    Raises:
        ImportError: if the ``mcp`` package is not installed.
    """
    server = _build_server(_server_class())
    for name, description, fn in TOOL_SPECS:
        server.tool(name=name, description=description)(fn)
    return server


def run_server(service: Optional[str] = None, api_url: Optional[str] = None) -> None:
    """Run the MDF MCP server on the stdio transport.

    Args:
        service: Service instance to read from (staging/prod/dev/local).
        api_url: Explicit API base URL, overriding ``service``.

    Raises:
        ImportError: if the ``mcp`` package is not installed.
    """
    if service or api_url:
        configure(service=service, api_url=api_url)
    # stderr only: stdout belongs to the MCP protocol.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    logger.info("Starting MDF MCP server on stdio (read-only, anonymous)")
    create_server().run(transport="stdio")
