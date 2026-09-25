"""`mdf search [QUERY]`."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import typer
from rich.markup import escape

from mdf.cli import theme
from mdf.cli.app import PANEL_EXPLORE, app
from mdf.cli.commands._shared import (
    _add_id_column,
    _add_title_column,
    _one_line,
    _or_dash,
)
from mdf.cli.formatting import (
    EXIT_AUTH,
    EXIT_NETWORK,
    EXIT_USAGE,
    api_spinner,
    chrome,
    classify_api_error,
    console,
    exit_code_for,
    is_success,
    json_fail,
    json_ok,
    status_code_of,
)
from mdf.cli.options import (
    ApiUrlOpt,
    DevUserOpt,
    JsonOpt,
    ServiceOpt,
    TokenOpt,
    open_client,
)
from mdf.core.backend_client import BackendClient
from mdf.core.config import resolve_service


def _render_search_facets(
    facets: Optional[Dict[str, Any]], active: Optional[Dict[str, Any]] = None
) -> None:
    """Print the backend's facet buckets, with the flag that selects each one.

    The backend returns display names (``Year``, ``Organization``, ``Authors``,
    ``Keywords``, ``Domains``); the matching CLI flags are singular, so the
    header spells out the flag to make the facets actionable rather than
    decorative.
    """
    if not facets or not isinstance(facets, dict):
        return

    flag_for = {
        "year": "--year",
        "organization": "--organization",
        "authors": "--author",
        "author": "--author",
        "keywords": "--keyword",
        "keyword": "--keyword",
        "domains": "--domain",
        "domain": "--domain",
    }
    selected = {
        name: {str(value) for value in values}
        for name, values in (active or {}).items()
    }

    rows: List[tuple] = []
    for display_name, buckets in facets.items():
        if not buckets:
            continue
        flag = flag_for.get(str(display_name).lower())
        chosen = selected.get((flag or "").lstrip("-"), set())
        rendered = []
        for bucket in buckets[:6]:
            if not isinstance(bucket, dict):
                continue
            value = str(bucket.get("value", ""))
            count = bucket.get("count")
            label = f"{escape(value)} [secondary]({theme.number(count)})[/secondary]"
            if value in chosen:
                label = f"[ok]{theme.OK}[/ok] {label}"
            rendered.append(label)
        if rendered:
            rows.append((str(display_name), flag, rendered))
    if not rows:
        return

    # The label column carries the *flag*, not the backend's display name: it
    # is what the reader has to type to apply the facet, and it fits.
    console.print("\n[bold]Facets[/bold] [secondary](filter with the flag shown)[/secondary]")
    table = theme.detail_table(label_width=15)
    for display_name, flag, rendered in rows:
        table.add_row(flag or display_name, "  ".join(rendered))
    console.print(table)



@app.command("search", rich_help_panel=PANEL_EXPLORE)
def search(
    query: Optional[str] = typer.Argument(
        None, help="Search query (omit to browse the newest published datasets)"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Page size (backend max 50)"),
    offset: int = typer.Option(0, "--offset", help="Skip the first N results (paging)"),
    sort: Optional[str] = typer.Option(
        None, "--sort", help="relevance (default), newest, or most_viewed"
    ),
    year: Optional[List[str]] = typer.Option(
        None, "--year", help="Filter by publication year, e.g. 2021 (repeatable)"
    ),
    organization: Optional[List[str]] = typer.Option(
        None, "--organization", help="Filter by organization, e.g. 'MDF Open' (repeatable)"
    ),
    author: Optional[List[str]] = typer.Option(
        None, "--author", help="Filter by author as 'Family, Given' (repeatable)"
    ),
    keyword: Optional[List[str]] = typer.Option(
        None, "--keyword", help="Filter by keyword/subject (repeatable)"
    ),
    domain: Optional[List[str]] = typer.Option(
        None, "--domain", help="Filter by scientific domain (repeatable)"
    ),
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help="Use vector semantic search (title + description embeddings). Datasets only.",
    ),
    json_output: JsonOpt = False,
    brief: bool = typer.Option(False, "--brief", "-b", help="Minimal JSON: title, authors, doi (requires --json)"),
    service: ServiceOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
    api_url: ApiUrlOpt = None,
):
    """Search public datasets.

    The query is optional: with no query this browses the newest published
    datasets, in the same shape as a keyword search. Filter flags take whole
    facet values (as printed under "Facets" / returned in `facets`) and are
    repeatable — repeating one ORs the values together.

    Examples:
        mdf search "perovskite"
        mdf search                                  # browse the newest datasets
        mdf search "band gap" --year 2021 --limit 5
        mdf search --organization "MDF Open" --sort newest
        mdf search "alloy" --author "Ward, Logan" --author "Wolverton, Chris"
        mdf search "perovskite" --json --brief
        mdf search "battery cathode materials" --semantic
    """
    filters = {
        "year": year,
        "organization": organization,
        "author": author,
        "keyword": keyword,
        "domain": domain,
    }
    active_filters = {name: values for name, values in filters.items() if values}

    def _usage(message: str, hint: str) -> None:
        if json_output:
            json_fail(kind="validation", message=message, hint=hint)
        console.print()
        console.print(theme.error_panel(escape(message), title="Bad option", hint=hint))
        raise typer.Exit(code=EXIT_USAGE)

    if sort and sort not in BackendClient.SEARCH_SORTS:
        _usage(
            f"Unsupported --sort value: {sort}",
            f"use one of {', '.join(BackendClient.SEARCH_SORTS)}",
        )
    if limit < 1:
        _usage("--limit must be at least 1", "e.g. --limit 20")
    if limit > 50:
        chrome(json_output).print("[warn]--limit capped at 50 (the service maximum); use --offset to page[/warn]")
        limit = 50
    if semantic and not query:
        _usage(
            "--semantic needs a query to embed.",
            "give it a phrase, or drop --semantic to browse the newest datasets",
        )
    if semantic and (active_filters or sort or offset):
        # /search/semantic takes only q + limit; silently dropping the filters
        # would hand back an unfiltered answer that looks filtered.
        _usage(
            "--semantic does not support facet filters, --sort or --offset.",
            "drop --semantic for faceted keyword search, or drop the filters",
        )

    resolved = resolve_service(service)
    with open_client(
        api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output
    ) as client:
        with api_spinner("Searching (semantic)..." if semantic else "Searching..."):
            if semantic:
                result = client.search_semantic(query, limit=limit)
            else:
                result = client.search(
                    query,
                    search_type="all",
                    limit=limit,
                    offset=offset,
                    sort=sort,
                    **filters,
                )

    if semantic:
        # Semantic search requires auth (it spends the server's OpenAI key).
        # An anonymous caller gets a 401 {"detail": ...} — surface a login hint.
        detail = str(result.get("detail") or result.get("error") or "")
        if not result.get("results") and any(
            kw in detail.lower() for kw in ("auth", "token", "credential", "401", "unauthorized")
        ):
            if json_output:
                json_fail(
                    result,
                    kind="auth",
                    message="Semantic search requires login.",
                    code=status_code_of(result) or 401,
                    hint=f"run `mdf login --service {resolved}`, then retry with --semantic",
                )
            console.print()
            console.print(theme.error_panel(
                "Semantic search requires login.",
                title="Login required",
                border="yellow",
                hint=f"run `mdf login --service {resolved}`, then retry with --semantic",
            ))
            raise typer.Exit(code=EXIT_AUTH)
        if not result.get("available", True):
            reason = result.get("reason") or "Semantic search unavailable"
            if json_output:
                # An unbuilt snapshot is a service-availability answer, not a
                # bad request: keep the payload, mark it as such.
                json_fail(
                    result,
                    kind="server",
                    message="Semantic search unavailable",
                    hint="keyword search still works: drop --semantic",
                )
            console.print()
            console.print(theme.error_panel(
                "Semantic search unavailable",
                title="Semantic search",
                border="yellow",
                detail=escape(str(reason)),
                hint="keyword search still works: drop --semantic",
            ))
            raise typer.Exit(code=EXIT_NETWORK)
        # Normalize shape so downstream rendering matches keyword search
        result.setdefault("query", query)
        result.setdefault("total", len(result.get("results") or []))

    if json_output:
        if not is_success(result):
            json_fail(result)
        if brief:
            # A backend that answers `"results": null` is an empty result set,
            # not a crash.
            items = [
                {
                    "source_id": item.get("source_id"),
                    "version": item.get("version"),
                    "title": item.get("title", ""),
                    "authors": item.get("authors", []),
                    "doi": item.get("doi"),
                }
                for item in result.get("results") or []
            ]
            json_ok({"total": result.get("total"), "offset": offset, "results": items})
        else:
            json_ok(result)
        return

    if not result.get("results") and not is_success(result):
        # A failed lookup is not an empty result set — say so, with the reason.
        info = classify_api_error(result)
        console.print()
        console.print(theme.error_panel(
            escape(str(info["message"])),
            title="Search failed",
            code=info["code"],
            detail=escape(str(info["detail"])) if info["detail"] else None,
            hint=info["hint"],
            border=info["border"] or "red",
        ))
        raise typer.Exit(code=exit_code_for(result))

    if result.get("results"):
        total = result.get("total", 0)
        shown = len(result["results"])
        subject = (
            f"[bold]results for[/bold] [accent]'{escape(str(result.get('query')))}'[/accent]"
            if result.get("query")
            else "[bold]newest datasets[/bold]"
        )
        console.print(f"\n{theme.SEARCH} [bold]Found[/bold] {theme.number(total)} {subject}\n")

        table = theme.table()
        # The ID comes first and is never truncated: it is what the next
        # command (`mdf show <id>`) needs, copied verbatim (CLI-U5).
        _add_id_column(
            table,
            "ID",
            [item.get("source_id") or item.get("stream_id") for item in result["results"]],
        )
        # No Type/Status columns: public search only returns published
        # datasets, and a full-width ID leaves no room for constant columns.
        table.add_column("Version", style="secondary", no_wrap=True)
        table.add_column("DOI", style="secondary", no_wrap=True)
        _add_title_column(table)

        for item in result["results"]:
            table.add_row(
                escape(str(item.get("source_id") or "")),
                escape(_or_dash(item.get("version"))),
                escape(_or_dash(item.get("doi"))),
                _one_line(item.get("title")),
            )

        console.print(table)
        _render_search_facets(result.get("facets"), active_filters)

        next_steps = []
        first = result["results"][0]
        if first.get("source_id"):
            next_steps.append(f"mdf show {escape(str(first['source_id']))}")
        if isinstance(total, int) and offset + shown < total:
            next_steps.append(f"--offset {offset + shown} for the next page")
        if next_steps:
            console.print(f"\n[hint]Next: {'  ·  '.join(next_steps)}[/hint]")
    else:
        subject = f"'{escape(query)}'" if query else "the current filters"
        hints = []
        if active_filters:
            hints.append(
                "drop a filter: "
                + ", ".join(f"--{name}" for name in sorted(active_filters))
            )
        if query:
            hints.append(
                f"broaden the query, e.g. `mdf search \"{escape(query.split()[0] if query.split() else query)}\"`"
            )
        if not semantic and query:
            hints.append("try `--semantic` for meaning-based matching")
        hints.append("list your own submissions with `mdf list`")
        console.print()
        console.print(theme.empty_panel(
            f"No results found for {subject}",
            title="no matches",
            hints=hints,
        ))
        _render_search_facets(result.get("facets"), active_filters)
