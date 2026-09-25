"""`mdf show ID` and `mdf related ID`."""

from __future__ import annotations

from typing import Optional

import typer
from rich.markup import escape

from mdf.cli import theme
from mdf.cli.app import PANEL_EXPLORE, app
from mdf.cli.commands._shared import (
    _add_id_column,
    _add_title_column,
    _one_line,
    _or_dash,
    _resolve_identifier_with_notice,
)
from mdf.cli.formatting import (
    EXIT_USAGE,
    api_spinner,
    console,
    emit_json_result,
    format_authors,
    format_status_badge,
    is_success,
    json_fail,
    require_success,
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


@app.command("related", rich_help_panel=PANEL_EXPLORE)
def related(
    source_id: str = typer.Argument(..., help="Source ID (or DOI) of the dataset"),
    by: str = typer.Option("author", "--by", help="Relation type: author | similar"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """List datasets related to a given dataset.

    - `--by author` (default): datasets sharing at least one author (ORCID-first,
      falls back to normalized name).
    - `--by similar`: nearest neighbors over the dataset embedding snapshot
      (title + description). Requires a built embedding snapshot.

    Examples:
        mdf related my_dataset_v1
        mdf related 10.18126/xxxx --by similar --limit 10
    """
    if by not in ("author", "similar"):
        if json_output:
            json_fail(
                kind="validation",
                message=f"Unsupported relation type: {by}",
                hint="use `--by author` or `--by similar`",
            )
        console.print(theme.error_panel(
            f"Unsupported relation type: {escape(str(by))}",
            title="Bad option",
            hint="use `--by author` or `--by similar`",
        ))
        raise typer.Exit(code=EXIT_USAGE)

    resolved = resolve_service(service)
    with open_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output) as client:
        resolved_id = _resolve_identifier_with_notice(client, source_id, json_mode=json_output)

        if by == "similar":
            spinner_text = "Finding similar datasets..."
        else:
            spinner_text = "Finding related datasets..."

        with api_spinner(spinner_text):
            if by == "similar":
                result = client.similar_by_embedding(resolved_id, limit=limit)
            else:
                result = client.related_by_author(resolved_id, limit=limit)

    if json_output:
        emit_json_result(result)
        return

    if not is_success(result):
        # "No relations" and "we could not ask" are different answers.
        require_success(result, error_prefix="Related failed")

    if by == "similar" and result.get("available") is False:
        reason = result.get("reason") or "Embedding snapshot unavailable."
        console.print()
        console.print(theme.empty_panel(
            escape(str(reason)),
            title="similarity unavailable",
            hints=["`mdf related <id> --by author` works without embeddings"],
        ))
        return

    results = result.get("results", [])
    if not results:
        console.print()
        if by == "similar" and result.get("reason"):
            console.print(theme.empty_panel(
                escape(str(result["reason"])),
                title="no similar datasets",
                hints=["try `--by author` instead"],
            ))
        else:
            console.print(theme.empty_panel(
                f"No related datasets found for {escape(str(resolved_id))}",
                title="no relations",
                hints=[
                    "try `--by similar` for embedding neighbors"
                    if by == "author"
                    else "try `--by author` for co-authored datasets",
                    "or search the whole catalog with `mdf search <query>`",
                ],
            ))
        return

    if by == "similar":
        console.print(
            f"\n[bold]{theme.number(len(results))} similar dataset(s)[/bold] "
            f"to {theme.ident(resolved_id)} [secondary](by embedding)[/secondary]\n"
        )
        table = theme.table()
        _add_id_column(table, "Source ID", [row.get("source_id") for row in results])
        table.add_column("Score", style="value", justify="right")
        table.add_column("Year", style="secondary", no_wrap=True, justify="right")
        table.add_column("DOI", style="secondary", no_wrap=True)
        _add_title_column(table)
        for row in results:
            score = row.get("score")
            score_str = f"{float(score):.3f}" if score is not None else "—"
            table.add_row(
                escape(str(row.get("source_id", ""))),
                score_str,
                escape(_or_dash(row.get("publication_year"))),
                escape(_or_dash(row.get("doi"))),
                _one_line(row.get("title")),
            )
        console.print(table)
        return

    console.print(
        f"\n[bold]{theme.number(len(results))} related dataset(s)[/bold] "
        f"sharing authors with {theme.ident(resolved_id)}\n"
    )
    table = theme.table()
    _add_id_column(table, "Source ID", [row.get("source_id") for row in results])
    table.add_column("Shared", style="value", justify="right")
    table.add_column("Year", style="secondary", no_wrap=True, justify="right")
    table.add_column("DOI", style="secondary", no_wrap=True)
    _add_title_column(table)
    for row in results:
        table.add_row(
            escape(str(row.get("source_id", ""))),
            escape(_or_dash(row.get("shared_authors"))),
            escape(_or_dash(row.get("publication_year"))),
            escape(_or_dash(row.get("doi"))),
            _one_line(row.get("title")),
        )
    console.print(table)



@app.command(rich_help_panel=PANEL_EXPLORE)
def show(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    cite: bool = typer.Option(False, "--cite", "-c", help="Include the APA citation"),
    files: bool = typer.Option(False, "--files", help="Include the file list"),
    sample: bool = typer.Option(False, "--sample", help="Include a sample of tabular data"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Show dataset details.

    Examples:
        mdf show my_dataset_v1
        mdf show my_dataset_v1 --cite
        mdf show my_dataset_v1 --files
        mdf show my_dataset_v1 --json
    """
    from mdf.cli.dataset_cmd import is_missing_profile, render_files

    resolved = resolve_service(service)
    with open_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output) as client:
        source_id = _resolve_identifier_with_notice(client, source_id, json_mode=json_output)
        with api_spinner("Loading dataset..."):
            result = client.get_card(source_id, version=version)
            citation_result = None
            files_result = None
            if cite and is_success(result):
                citation_result = client.get_citation(source_id, format="apa", version=version)
            if (files or sample) and is_success(result):
                files_result = client.dataset_sample(source_id) if sample else client.dataset_preview(source_id)

    if json_output:
        # The API response already is {"success", "card", "permissions"}: emit
        # it as-is (never nested under another "card"), plus what was asked for.
        output = dict(result)
        if citation_result:
            output["citation"] = citation_result
        if files_result is not None:
            output["sample" if sample else "files"] = (
                None if is_missing_profile(files_result) else dict(files_result)
            )
        emit_json_result(result, output)
        return

    if not is_success(result):
        require_success(result, error_prefix="Show failed")

    c = result.get("card", {})

    console.print()
    console.print(theme.panel(
        f"[bold]{escape(str(c.get('title', 'Untitled')))}[/bold]\n"
        f"[secondary]{escape(str(c.get('description', 'No description')))}[/secondary]",
        title=f"{theme.SCIENCE} {escape(str(c.get('source_id', '')))}",
        subtitle=f"v{c.get('version', '1.0')}",
    ))

    table = theme.detail_table()

    if c.get("authors"):
        table.add_row("Authors", escape(format_authors(c["authors"])))
    if c.get("publisher"):
        table.add_row("Publisher", escape(str(c["publisher"])))
    if c.get("publication_year"):
        table.add_row("Year", theme.number(c["publication_year"]))
    if c.get("organization"):
        table.add_row("Organization", escape(str(c["organization"])))
    if c.get("keywords"):
        table.add_row("Keywords", escape(", ".join(c["keywords"])))
    if c.get("doi"):
        table.add_row("DOI", f"https://doi.org/{escape(str(c['doi']))}")
    if c.get("license"):
        table.add_row("License", escape(str(c["license"])))
    table.add_row("Status", format_status_badge(c.get("status", "unknown")))

    ml = c.get("ml") or {}
    if ml:
        if ml.get("task_type"):
            table.add_row("ML Task", escape(str(ml["task_type"])))
        if ml.get("data_format"):
            table.add_row("ML Format", escape(str(ml["data_format"])))

    console.print(table)

    stats = c.get("stats", {})
    if stats:
        console.print(
            f"[label]Files:[/label] {theme.number(stats.get('data_sources_count', 0))} sources "
            f"[secondary]|[/secondary] [label]Types:[/label] "
            f"{escape(', '.join(stats.get('file_types', [])))}"
        )

    if citation_result and is_success(citation_result):
        apa = citation_result.get("apa", "")
        if apa:
            console.print()
            console.print(theme.panel(escape(str(apa)), title="Citation"))

    if files_result is not None:
        render_files(files_result, source_id, sample=sample)

    next_steps = [f"mdf cite {source_id}", f"mdf clone {source_id}"]
    if not files:
        next_steps.insert(0, f"mdf show {source_id} --files")
    console.print(f"\n[hint]Next: {'  ·  '.join(next_steps)}[/hint]", soft_wrap=True)
    console.print()
