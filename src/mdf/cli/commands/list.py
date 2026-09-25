"""`mdf list [--all]`."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import typer
from rich.markup import escape

from mdf.cli import theme
from mdf.cli.app import PANEL_PUBLISH, app
from mdf.cli.commands._shared import (
    _add_id_column,
    _add_title_column,
    _one_line,
    _or_dash,
    _parse_submission_mdata,
)
from mdf.cli.formatting import (
    api_spinner,
    classify_api_error,
    console,
    exit_code_for,
    format_status_badge,
    is_success,
    json_fail,
    json_ok,
)
from mdf.cli.options import (
    ApiUrlOpt,
    DevUserOpt,
    JsonOpt,
    ServiceOpt,
    TokenOpt,
    open_client,
)
from mdf.core.config import resolve_service

#: Hard stop on how many ``/submissions`` pages one `mdf list` will walk, so a
#: broad client-side ``--search`` can never turn into an unbounded crawl.
_LIST_MAX_PAGES = 20



@app.command("list", rich_help_panel=PANEL_PUBLISH)
def list_datasets(
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
    status_filter: Optional[str] = typer.Option(
        None, "--status", help="Filter by status server-side (comma-separated for several)"
    ),
    search: Optional[str] = typer.Option(None, "--search", help="Filter by title, source ID, or DOI"),
    show_all: bool = typer.Option(
        False, "--all", help="Include older versions and deleted/withdrawn records"
    ),
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
    json_output: JsonOpt = False,
):
    """List your submitted datasets.

    `--limit` and `--status` are applied by the backend, and pages are followed
    via the response's `next_key` cursor until `--limit` records are collected —
    so a dataset outside the first page is still found. By default only the
    latest version of each dataset is shown and deleted/withdrawn records are
    hidden (`--all` shows everything; `--status` names statuses explicitly).
    `--search` and these defaults are applied to each page as it arrives.

    Under `--json` the echoed `next_key` is a safe resume point: if `--limit`
    filled up part-way through a page, it points at that same page (flagged
    `"partial_page": true`) rather than past the records that were never read.
    A page that fails mid-walk is reported as a failure (non-zero exit, error
    envelope) with whatever was already collected under `"partial": true`.

    Examples:
        mdf list
        mdf list --limit 50
        mdf list --status published,rejected
        mdf list --search perovskite
        mdf list --all
    """
    resolved = resolve_service(service)
    with open_client(public=False, api_url=api_url, token=token, service=resolved, dev_user=dev_user) as client:

        wanted_statuses = {
            part.strip() for part in (status_filter or "").split(",") if part.strip()
        }
        query = (search or "").strip().lower()
        latest_only = not show_all
        hidden_statuses = set() if (show_all or wanted_statuses) else {"deleted", "withdrawn"}
        # `--search` and the latest/hidden defaults have no server-side
        # equivalent, so they need material to filter: ask for full pages then.
        # Otherwise the page size is just what the caller asked for.
        client_side_filtering = bool(query or latest_only or hidden_statuses)
        page_size = 50 if client_side_filtering else max(1, min(limit, 50))

        filtered_submissions: List[tuple] = []
        first_result: Optional[Dict[str, Any]] = None
        result: Dict[str, Any] = {}
        start_key: Optional[str] = None
        # The cursor a caller may resume from. It only moves past a page once that
        # page has been read to the end: handing back the *next* page's cursor
        # after stopping mid-page would permanently skip the records we never
        # looked at.
        resume_key: Optional[str] = None
        partial_page = False
        # Set when a page request fails. Pages already collected are real data, but
        # the listing is then incomplete and must never exit 0.
        page_error: Optional[Dict[str, Any]] = None
        pages = 0

        with api_spinner("Loading datasets..."):
            while True:
                result = client.submissions(
                    status=status_filter or None,
                    limit=page_size,
                    start_key=start_key,
                    include_counts=(pages == 0),
                )
                if first_result is None:
                    first_result = result
                if not is_success(result):
                    page_error = result
                    break

                page = result.get("submissions", []) or []
                scanned_whole_page = True
                for index, sub in enumerate(page):
                    mdata = _parse_submission_mdata(sub)
                    # Keep the client-side status check as a backstop: a backend
                    # that ignores ?status= must not silently widen the result set.
                    if wanted_statuses and sub.get("status", "") not in wanted_statuses:
                        continue
                    if sub.get("status", "") in hidden_statuses:
                        continue
                    if latest_only and mdata.get("latest") is False:
                        continue
                    if query:
                        haystack = " ".join(
                            str(value or "")
                            for value in (
                                sub.get("source_id"),
                                sub.get("doi"),
                                sub.get("dataset_doi"),
                                sub.get("title"),
                                mdata.get("title"),
                            )
                        ).lower()
                        if query not in haystack:
                            continue
                    filtered_submissions.append((sub, mdata))
                    if len(filtered_submissions) >= limit:
                        # Anything after `index` on this page is still unread.
                        scanned_whole_page = index == len(page) - 1
                        break

                pages += 1
                if not scanned_whole_page:
                    # `--limit` filled up part-way through a page. Resume from the
                    # START of this page: re-reading a record is recoverable,
                    # skipping one is not.
                    resume_key = start_key
                    partial_page = True
                    break
                start_key = result.get("next_key")
                resume_key = start_key
                if not start_key or len(filtered_submissions) >= limit or pages >= _LIST_MAX_PAGES:
                    break

    collected = [sub for sub, _mdata in filtered_submissions]

    if page_error is not None:
        # A mid-stream page failure is still a failure: report it with the
        # mapped exit code and say how much of the listing was read.
        partial = bool(collected)
        if json_output:
            json_fail(
                page_error,
                extra={
                    "partial": partial,
                    "pages_read": pages,
                    "submissions": collected,
                    "count": len(collected),
                    "next_key": resume_key,
                },
            )
        # Distinguish "you have no datasets" from "we could not ask".
        info = classify_api_error(page_error)
        detail_parts = []
        if info["detail"]:
            detail_parts.append(escape(str(info["detail"])))
        if partial:
            detail_parts.append(
                f"Partial results: {len(collected)} dataset(s) read from "
                f"{pages} page(s) before the failure — the listing is incomplete."
            )
        console.print()
        console.print(theme.error_panel(
            escape(str(info["message"])),
            title="Listing failed",
            code=info["code"],
            detail="\n".join(detail_parts) or None,
            hint=info["hint"],
            border=info["border"] or "red",
        ))
        raise typer.Exit(code=exit_code_for(page_error))

    base_result = first_result if first_result is not None else result
    if json_output:
        filtered_result = dict(base_result)
        filtered_result["submissions"] = collected
        # `total` is the backend's overall count (from include_counts) and stays
        # authoritative; `count` is how many records this response carries.
        filtered_result["count"] = len(collected)
        # Echo the cursor we stopped at so a caller can resume paging.
        filtered_result["next_key"] = resume_key
        if partial_page:
            # `next_key` re-reads the page we stopped inside rather than
            # skipping past the records after the cut.
            filtered_result["partial_page"] = True
        json_ok(filtered_result)
        return

    if not filtered_submissions:
        filters_used = bool(status_filter or search)
        hints = ["publish your first dataset with `mdf publish --submit`"]
        if filters_used:
            hints.insert(0, "loosen the filters (--status / --search)")
        elif not show_all:
            hints.insert(0, "`mdf list --all` also shows older versions and deleted/withdrawn records")
        else:
            hints.append("not seeing your data? check `mdf whoami`")
        console.print()
        console.print(theme.empty_panel(
            "No datasets found.", title="no datasets", hints=hints
        ))
        return

    console.print(
        f"\n[bold]Your datasets[/bold] [secondary]([/secondary]{theme.number(len(filtered_submissions))}[secondary])[/secondary]\n"
    )
    table = theme.table()
    _add_id_column(table, "Source ID", [sub.get("source_id") for sub, _ in filtered_submissions])
    table.add_column("Version", style="secondary", no_wrap=True, justify="right")
    table.add_column("Status", no_wrap=True)
    table.add_column("Updated", style="secondary", no_wrap=True)
    _add_title_column(table)

    for sub, mdata in filtered_submissions:
        title_str = mdata.get("title") or sub.get("title") or "Untitled"
        status_str = format_status_badge(sub.get("status", ""))
        table.add_row(
            escape(str(sub.get("source_id", ""))),
            escape(_or_dash(sub.get("version"))),
            status_str,
            escape(theme.relative_time(sub.get("updated_at") or sub.get("created_at"))),
            _one_line(title_str),
        )
    console.print(table)
    console.print(
        f"\n[hint]Next: mdf status {filtered_submissions[0][0].get('source_id', '<id>')}[/hint]",
        soft_wrap=True,
    )
    if resume_key or partial_page:
        console.print(
            f"\n[hint]More records remain — raise --limit (currently {limit}) to fetch them.[/hint]"
        )
