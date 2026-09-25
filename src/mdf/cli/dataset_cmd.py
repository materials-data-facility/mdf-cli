"""`mdf dataset edit|withdraw|resubmit|diff|open`, plus `cite` and `versions` (registered top-level)."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mdf.core.backend_client import BackendClient
from mdf.core.config import resolve_service
from mdf.cli import theme
from mdf.cli.options import ApiUrlOpt, DevUserOpt, JsonOpt, ServiceOpt, TokenOpt, open_client
from mdf.cli.formatting import (
    EXIT_USAGE,
    api_spinner,
    confirm_or_abort,
    console,
    err_console,
    emit_json_result,
    format_status_badge,
    interactive_stdin,
    is_success,
    json_fail,
    require_success,
    resolve_identifier_with_notice,
    status_code_of,
)

app = typer.Typer(
    help="Manage your datasets: edit, withdraw, resubmit, diff, open",
    no_args_is_help=True,
)


def _resolve_identifier_with_notice(
    client: BackendClient, raw_id: str, *, json_mode: bool = False
) -> str:
    """Thin alias for the shared resolver — see ``formatting.resolve_identifier_with_notice``."""
    return resolve_identifier_with_notice(client, raw_id, json_mode=json_mode)


_human_size = theme.human_size


def _interactive_stdin() -> bool:
    """True when there is a human on the other end who can answer a prompt."""
    return interactive_stdin()


def _confirm_or_abort(question: str, *, yes: bool, json_mode: bool = False) -> None:
    """Thin alias for the shared gate — see ``formatting.confirm_or_abort``."""
    confirm_or_abort(
        question, yes=yes, json_mode=json_mode, is_interactive=_interactive_stdin
    )


def _copy_to_clipboard(text: str) -> None:
    import subprocess
    import shutil

    if shutil.which("pbcopy"):
        cmd = ["pbcopy"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    elif shutil.which("xsel"):
        cmd = ["xsel", "--clipboard", "--input"]
    else:
        console.print("[secondary]Clipboard not available[/secondary]")
        return

    try:
        subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
        console.print(theme.mark("ok", "Copied to clipboard", style="secondary"))
    except Exception:
        pass


#: ``--format`` value -> panel title. The keys are also the backend's format names.
CITATION_FORMATS = {
    "apa": "APA Citation",
    "bibtex": "BibTeX",
    "ris": "RIS",
    "datacite": "DataCite XML",
}


def cite(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    format: str = typer.Option("apa", "--format", "-f", help="Citation format: apa, bibtex, ris, datacite"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write the citation text to FILE instead of stdout"
    ),
    copy: bool = typer.Option(False, "--copy", help="Copy citation to clipboard"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Get citation for a dataset.

    On a terminal the citation is shown in a panel; piped or redirected, stdout
    carries the citation text only.

    Examples:
        mdf cite my_dataset                       # APA format
        mdf cite my_dataset -f bibtex -o ref.bib  # BibTeX file for papers
        mdf cite my_dataset -f bibtex >> refs.bib # plain text when redirected
        mdf cite my_dataset --copy                # Copy to clipboard
    """
    format = (format or "").strip().lower()
    if format not in CITATION_FORMATS:
        message = f"Unknown citation format: {format!r}"
        hint = f"choose one of: {', '.join(CITATION_FORMATS)}"
        if json_output:
            json_fail(kind="validation", message=message, hint=hint)
        console.print(theme.error_panel(escape(message), title="Invalid --format", hint=hint))
        raise typer.Exit(code=EXIT_USAGE)

    resolved = resolve_service(service)
    with open_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output) as client:
        source_id = _resolve_identifier_with_notice(client, source_id, json_mode=json_output)
        with api_spinner("Fetching citation..."):
            result = client.get_citation(source_id, format=format, version=version)

    if json_output:
        emit_json_result(result)
        return

    if not is_success(result):
        require_success(result, error_prefix="Citation failed")

    citation_text = str(result.get(format) or result.get("apa") or "")

    if copy:
        _copy_to_clipboard(citation_text)

    if output is not None:
        output.write_text(citation_text.rstrip("\n") + "\n", encoding="utf-8")
        err_console.print(
            theme.mark("ok", f"Wrote {CITATION_FORMATS[format]} to {escape(str(output))}", style="ok")
        )
        return

    if not console.is_terminal:
        # Piped or redirected: the citation text only — no panel, no wrapping.
        print(citation_text.rstrip("\n"))
        return

    # Structured citation formats get their lexer; APA is prose.
    lexers = {"bibtex": "bibtex", "ris": "text", "datacite": "xml"}
    lexer = lexers.get(format)
    body = theme.syntax(citation_text, lexer, word_wrap=True) if lexer else escape(citation_text)

    console.print()
    console.print(theme.panel(body, title=CITATION_FORMATS[format], subtitle=source_id))
    console.print()


@app.command("open")
def open_dataset(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    url: bool = typer.Option(False, "--url", help="Print URL instead of opening browser"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Open a dataset in the browser.

    Examples:
        mdf dataset open my_dataset_v1
        mdf dataset open my_dataset_v1 --url
    """
    resolved = resolve_service(service)
    with open_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user) as client:
        source_id = _resolve_identifier_with_notice(client, source_id, json_mode=url)
        with api_spinner("Loading dataset..."):
            result = client.get_card(source_id, version=version)

    if not is_success(result):
        require_success(result, error_prefix="Open failed")

    card = result.get("card", {})
    doi = card.get("doi")
    if doi:
        target_url = f"https://doi.org/{doi}"
    else:
        target_url = f"https://materialsdatafacility.org/datasets/{source_id}"

    if url:
        # `--url` is machine output: the URL is the whole payload on stdout.
        print(target_url)
    else:
        console.print(f"[secondary]Opening:[/secondary] {escape(target_url)}")
        webbrowser.open(target_url)


def is_missing_profile(result: dict) -> bool:
    """A 404 that only means "no file profile yet" (most migrated records).

    That is an expected empty state, not a failure — unlike a 404 for a
    nonexistent dataset, which keeps normal error handling.
    """
    text = str(result.get("detail") or result.get("error") or "").lower()
    return status_code_of(result) == 404 and "profile" in text


def render_files(result: dict, source_id: str, *, sample: bool = False) -> None:
    """Render ``GET /preview/{id}`` (file list) or ``/preview/{id}/sample`` for humans."""
    if is_missing_profile(result):
        console.print()
        console.print(theme.empty_panel(
            "No file profile exists for this dataset\n"
            "(profiles are not yet generated for migrated records).",
            title="no profile",
            hints=[f"mdf clone {source_id} --plan --files — to list the files from storage"],
        ))
        return
    if not is_success(result):
        require_success(result, error_prefix="Could not load the file list")

    if sample:
        rows = result.get("rows") or result.get("data", [])
        columns = result.get("columns", [])
        if not rows:
            console.print()
            console.print(theme.empty_panel(
                "No sample data available.",
                title="no sample",
                hints=[f"list the files instead: `mdf show {source_id} --files`"],
            ))
            return
        if not columns and rows:
            first = rows[0]
            if isinstance(first, dict):
                columns = list(first.keys())
        console.print(f"\n[bold]Sample[/bold] [secondary]({len(rows)} rows)[/secondary]\n")
        table = theme.table()
        for col in columns[:10]:
            table.add_column(escape(str(col)), max_width=30)
        for row in rows[:20]:
            if isinstance(row, dict):
                table.add_row(*[escape(str(row.get(c, ""))[:30]) for c in columns[:10]])
            elif isinstance(row, (list, tuple)):
                table.add_row(*[escape(str(v)[:30]) for v in row[:10]])
        console.print(table)
        if len(rows) > 20:
            console.print(f"[secondary]... and {len(rows) - 20} more rows[/secondary]")
        return

    files = result.get("files", result.get("data_sources", []))
    if not files:
        console.print()
        console.print(theme.empty_panel("No file information available.", title="no files"))
        return
    console.print(f"\n[bold]Files[/bold] [secondary]({len(files)})[/secondary]\n")
    table = theme.table()
    table.add_column("Name", max_width=60, overflow="fold")
    table.add_column("Size", style="value", no_wrap=True, justify="right")
    table.add_column("Type", style="secondary", no_wrap=True)
    for f in files:
        if not isinstance(f, dict):
            table.add_row(escape(str(f)), "", "")
            continue
        name = f.get("filename") or f.get("name") or f.get("path", "?")
        size = f.get("size") or f.get("length", "")
        if isinstance(size, (int, float)) and size > 0:
            size = _human_size(size)
        else:
            size = str(size) if size else "—"
        ftype = f.get("mime_type") or f.get("type") or "—"
        table.add_row(escape(str(name)), escape(str(size)), escape(str(ftype)))
    console.print(table)


def versions(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max versions to return"),
    offset: int = typer.Option(0, "--offset", help="Skip first N versions"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Show version history for a dataset.

    Examples:
        mdf versions my_dataset_v1
    """
    resolved = resolve_service(service)
    with open_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output) as client:
        source_id = _resolve_identifier_with_notice(client, source_id, json_mode=json_output)
        with api_spinner("Loading versions..."):
            result = client.versions(source_id, limit=limit, offset=offset)

    if json_output:
        emit_json_result(result)
        return

    if not is_success(result):
        # A 404/401/500 is a failure, not an empty version history: route it
        # through the shared exit-code table instead of exiting 0.
        require_success(result, error_prefix="Versions failed")

    version_list = result.get("versions") or []
    if not version_list:
        console.print()
        console.print(theme.empty_panel(
            "No versions found.",
            title="no versions",
            hints=[f"confirm the dataset exists with `mdf show {source_id}`"],
        ))
        return

    console.print(f"\n[bold]Versions for[/bold] {theme.ident(source_id)}\n")

    table = theme.table()
    table.add_column("Version", no_wrap=True, justify="right", style="id")
    table.add_column("Title", max_width=40)
    table.add_column("Status", no_wrap=True)
    table.add_column("DOI", style="secondary")
    table.add_column("Updated", style="secondary", no_wrap=True)

    for v in version_list:
        doi = v.get("doi") or ""
        if doi:
            doi = f"https://doi.org/{doi}"
        table.add_row(
            escape(str(v.get("version", ""))),
            escape(str(v.get("title", ""))),
            format_status_badge(v.get("status", "")),
            escape(str(doi)),
            escape(str(v.get("updated_at") or v.get("created_at") or "")[:19]),
        )
    console.print(table)

    dataset_doi = result.get("dataset_doi")
    if dataset_doi:
        console.print(f"\n[label]Dataset DOI:[/label] https://doi.org/{escape(str(dataset_doi))}")
    next_steps = [f"mdf show {source_id}"]
    if len(version_list) > 1:
        next_steps.append(
            f"mdf dataset diff {source_id} --from {version_list[0].get('version')} "
            f"--to {version_list[-1].get('version')}"
        )
    console.print(f"[hint]Next: {'  ·  '.join(next_steps)}[/hint]")
    console.print()


@app.command()
def diff(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    from_version: str = typer.Option(..., "--from", help="From version"),
    to_version: str = typer.Option(..., "--to", help="To version"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Show metadata diff between two versions.

    Examples:
        mdf dataset diff my_dataset_v1 --from 1.0 --to 2.0
    """
    resolved = resolve_service(service)
    with open_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output) as client:
        source_id = _resolve_identifier_with_notice(client, source_id, json_mode=json_output)
        with api_spinner("Loading diff..."):
            result = client.version_diff(source_id, from_version, to_version)

    if json_output:
        emit_json_result(result)
        return

    if not is_success(result):
        require_success(result, error_prefix="Diff failed")

    d = result.get("diff", {})
    fv = result.get("from_version", {})
    tv = result.get("to_version", {})

    console.print(
        f"\n[bold]Diff[/bold] {theme.ident(source_id)}  "
        f"[secondary]v{escape(str(fv.get('version', from_version)))} → "
        f"v{escape(str(tv.get('version', to_version)))}[/secondary]\n"
    )

    table = theme.table()
    table.add_column("Change", no_wrap=True, width=8)
    table.add_column("Field", no_wrap=True)
    table.add_column("Value")

    for field, value in d.get("added", {}).items():
        table.add_row("[ok]+ added[/ok]", escape(str(field)), escape(str(value)[:80]))
    for field, value in d.get("removed", {}).items():
        table.add_row("[err]- removed[/err]", escape(str(field)), escape(str(value)[:80]))
    for field, change in d.get("changed", {}).items():
        table.add_row(
            "[warn]~ changed[/warn]",
            escape(str(field)),
            f"{escape(str(change.get('from', ''))[:35])} [secondary]→[/secondary] "
            f"{escape(str(change.get('to', ''))[:35])}",
        )

    console.print(table)

    unchanged = d.get("unchanged", [])
    if unchanged:
        console.print(f"\n[secondary]Unchanged: {escape(', '.join(unchanged))}[/secondary]")
    console.print()


@app.command()
def edit(
    source_id: str = typer.Argument(..., help="Source ID of dataset to edit"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="New title"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="New description"),
    keywords: Optional[List[str]] = typer.Option(None, "--keyword", "-k", help="Keywords (repeatable)"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Target version"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Edit metadata on a submission.

    Examples:
        mdf dataset edit my_dataset_v1 --title "New Title"
        mdf dataset edit my_dataset_v1 --keyword "XRD" --keyword "diffraction"
    """
    resolved = resolve_service(service)
    fields = {}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if keywords is not None:
        fields["keywords"] = keywords

    if not fields:
        message = "No fields provided to edit."
        hint = "use --title, --description, or --keyword"
        if json_output:
            json_fail(kind="validation", message=message, hint=hint)
        console.print(theme.error_panel(
            message,
            title="Nothing to edit",
            hint=hint,
        ))
        raise typer.Exit(code=EXIT_USAGE)

    with open_client(public=False, api_url=api_url, token=token, service=resolved, dev_user=dev_user) as client:
        with api_spinner("Editing metadata..."):
            result = client.edit_metadata(source_id, version=version, **fields)

    if json_output:
        emit_json_result(result)
        return

    if is_success(result):
        detail = theme.detail_table(label_width=12)
        detail.add_row("Dataset", theme.ident(source_id))
        if result.get("updated_fields"):
            detail.add_row("Fields", escape(", ".join(result["updated_fields"])))
        if result.get("new_version"):
            detail.add_row("New version", theme.ident(result["new_version"]))
        console.print()
        console.print(theme.success_panel(detail, title="Metadata updated"))
    else:
        require_success(result, error_prefix="Edit failed")


@app.command()
def withdraw(
    source_id: str = typer.Argument(..., help="Source ID of dataset to withdraw"),
    reason: Optional[str] = typer.Option(None, "--reason", "-r", help="Withdrawal reason"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Target version"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Withdraw a pending_curation submission.

    Examples:
        mdf dataset withdraw my_dataset_v1
        mdf dataset withdraw my_dataset_v1 --reason "Duplicate submission"
    """
    resolved = resolve_service(service)
    _confirm_or_abort(
        f"Withdraw {source_id}? This removes it from the curation queue.",
        yes=yes,
        json_mode=json_output,
    )
    with open_client(public=False, api_url=api_url, token=token, service=resolved, dev_user=dev_user) as client:
        source_id = _resolve_identifier_with_notice(client, source_id, json_mode=json_output)
        with api_spinner("Withdrawing..."):
            result = client.withdraw(source_id, reason=reason or "", version=version)

    if json_output:
        emit_json_result(result)
        return

    if is_success(result):
        console.print()
        console.print(theme.panel(
            f"{theme.ident(source_id)} is no longer in the curation queue.\n"
            f"[label]Status:[/label] {format_status_badge('withdrawn')}",
            title=f"{theme.ACTIVE} Withdrawn",
            border="yellow",
            title_style="warn",
        ))
    else:
        require_success(result, error_prefix="Withdraw failed")


@app.command()
def resubmit(
    source_id: str = typer.Argument(..., help="Source ID of rejected dataset to resubmit"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="Notes for the curator"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Target version"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Resubmit a rejected dataset back to the curation queue.

    Examples:
        mdf dataset resubmit my_dataset_v1 --notes "Fixed title and added authors"
        mdf dataset resubmit my_dataset_v1 --yes          # no confirmation prompt
    """
    resolved = resolve_service(service)
    # Resubmitting puts the dataset back in front of curators, so it confirms
    # like `withdraw` does — and `--yes` is what makes it scriptable.
    _confirm_or_abort(
        f"Resubmit {source_id} to the curation queue?",
        yes=yes,
        json_mode=json_output,
    )
    with open_client(public=False, api_url=api_url, token=token, service=resolved, dev_user=dev_user) as client:
        source_id = _resolve_identifier_with_notice(client, source_id, json_mode=json_output)
        with api_spinner("Resubmitting..."):
            result = client.resubmit(source_id, notes=notes or "", version=version)

    if json_output:
        emit_json_result(result)
        return

    if is_success(result):
        detail = theme.detail_table(label_width=9)
        detail.add_row("Dataset", theme.ident(source_id))
        detail.add_row("Status", format_status_badge("pending_curation"))
        console.print()
        console.print(theme.success_panel(
            detail, title="Resubmitted", subtitle="back in the curation queue"
        ))
    else:
        require_success(result, error_prefix="Resubmit failed")
