"""Dataset utility subcommands: cite, open, preview, versions, diff, edit, withdraw, resubmit."""

from __future__ import annotations

import json
import webbrowser
from typing import List, Optional

import typer
from rich.markup import escape

from mdf.core.backend_client import BackendClient
from mdf.core.config import resolve_service
from mdf.cli import theme
from mdf.cli.formatting import (
    api_spinner,
    console,
    format_status_badge,
    is_success,
    read_client,
    require_success,
    status_code_of,
)
from mdf.cli.preflight import resolve_dataset_identifier

app = typer.Typer(
    help="Dataset utilities (cite, open, preview, versions, ...)",
    no_args_is_help=True,
)


def _resolve_identifier_with_notice(client: BackendClient, raw_id: str) -> str:
    resolved_id = resolve_dataset_identifier(raw_id, client)
    if resolved_id != raw_id:
        console.print(
            f"[secondary]Resolved DOI to source ID:[/secondary] {theme.ident(resolved_id)}"
        )
    return resolved_id


_human_size = theme.human_size


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


@app.command()
def cite(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    format: str = typer.Option("apa", "--format", "-f", help="Citation format: bibtex, ris, apa, datacite"),
    copy: bool = typer.Option(False, "--copy", help="Copy citation to clipboard"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Get citation for a dataset.

    Examples:
        mdf dataset cite my_dataset                       # APA format
        mdf dataset cite my_dataset -f bibtex             # BibTeX for papers
        mdf dataset cite my_dataset --copy                # Copy to clipboard
    """
    resolved = resolve_service(service)
    client = read_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user)
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Fetching citation..."):
        result = client.get_citation(source_id, format=format, version=version)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
        return

    if not is_success(result):
        require_success(result, error_prefix="Citation failed")

    citation_text = result.get(format) or result.get("apa", "")

    if copy:
        _copy_to_clipboard(citation_text)

    # Structured citation formats get their lexer; APA is prose.
    lexers = {"bibtex": "bibtex", "ris": "text", "datacite": "xml"}
    titles = {
        "bibtex": "BibTeX",
        "ris": "RIS",
        "datacite": "DataCite XML",
        "apa": "APA Citation",
    }
    title = titles.get(format, "APA Citation")
    lexer = lexers.get(format)
    body = theme.syntax(citation_text, lexer, word_wrap=True) if lexer else escape(citation_text)

    console.print()
    console.print(theme.panel(body, title=title, subtitle=source_id))
    console.print()


@app.command("open")
def open_dataset(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    url: bool = typer.Option(False, "--url", help="Print URL instead of opening browser"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Open a dataset in the browser.

    Examples:
        mdf dataset open my_dataset_v1
        mdf dataset open my_dataset_v1 --url
    """
    resolved = resolve_service(service)
    client = read_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user)
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Loading dataset..."):
        result = client.get_card(source_id, version=version)
    client.close()

    if not is_success(result):
        require_success(result, error_prefix="Open failed")

    card = result.get("card", {})
    doi = card.get("doi")
    if doi:
        target_url = f"https://doi.org/{doi}"
    else:
        target_url = f"https://materialsdatafacility.org/datasets/{source_id}"

    if url:
        print(target_url)
    else:
        console.print(f"[secondary]Opening:[/secondary] {escape(target_url)}")
        webbrowser.open(target_url)


@app.command()
def preview(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    sample: bool = typer.Option(False, "--sample", help="Show tabular data sample"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Preview a dataset's files and data.

    Examples:
        mdf dataset preview my_dataset_v1
        mdf dataset preview my_dataset_v1 --sample
    """
    resolved = resolve_service(service)
    client = read_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user)
    source_id = _resolve_identifier_with_notice(client, source_id)

    if sample:
        with api_spinner("Loading sample..."):
            result = client.dataset_sample(source_id)
    else:
        with api_spinner("Loading preview..."):
            result = client.dataset_preview(source_id)
    client.close()

    # A missing profile is an expected state, not a failure: most migrated
    # records have no generated profile yet, so the API 404s with "No profile
    # found". Only that specific 404 is friendly — a 404 for a nonexistent
    # dataset (typo'd source_id) must keep normal error handling.
    _payload_text = str(result.get("detail") or result.get("error") or "").lower()
    if status_code_of(result) == 404 and "profile" in _payload_text:
        if json_output:
            print(json.dumps(result, indent=2))
            return
        console.print()
        console.print(theme.empty_panel(
            "No file profile exists for this dataset\n"
            "(profiles are not yet generated for migrated records).",
            title="no profile",
            hints=[
                f"mdf show {source_id} — for metadata",
                f"mdf clone {source_id} — to download the files",
            ],
        ))
        return

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
        return

    if not is_success(result):
        require_success(result, error_prefix="Preview failed")

    if sample:
        rows = result.get("rows") or result.get("data", [])
        columns = result.get("columns", [])
        if not rows:
            console.print()
            console.print(theme.empty_panel(
                "No sample data available.",
                title="no sample",
                hints=[f"list the files instead: `mdf dataset preview {source_id}`"],
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
    else:
        files = result.get("files", result.get("data_sources", []))
        if not files:
            console.print()
            console.print(theme.empty_panel(
                "No file information available.",
                title="no files",
                hints=[f"see the metadata with `mdf show {source_id}`"],
            ))
            return
        console.print(f"\n[bold]Files[/bold] [secondary]({len(files)})[/secondary]\n")
        table = theme.table()
        table.add_column("Name", max_width=50)
        table.add_column("Size", style="value", no_wrap=True, justify="right")
        table.add_column("Type", style="secondary", no_wrap=True)
        for f in files:
            name = f.get("filename") or f.get("name") or f.get("path", "?")
            size = f.get("size") or f.get("length", "")
            if isinstance(size, (int, float)) and size > 0:
                size = _human_size(size)
            else:
                size = str(size) if size else ""
            ftype = f.get("mime_type") or f.get("type") or ""
            table.add_row(escape(str(name)), escape(str(size)), escape(str(ftype)))
        console.print(table)


@app.command()
def versions(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max versions to return"),
    offset: int = typer.Option(0, "--offset", help="Skip first N versions"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Show version history for a dataset.

    Examples:
        mdf dataset versions my_dataset_v1
    """
    resolved = resolve_service(service)
    client = read_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user)
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Loading versions..."):
        result = client.versions(source_id, limit=limit, offset=offset)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        return

    if not is_success(result):
        message = result.get("error") or result.get("detail") or "No versions found"
        console.print()
        console.print(theme.empty_panel(
            escape(str(message)),
            title="no versions",
            hints=[f"confirm the dataset exists with `mdf show {source_id}`"],
        ))
        return

    version_list = result.get("versions", [])
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
    console.print(
        f"[hint]Next: mdf show {source_id} | mdf dataset diff {source_id} "
        f"--from 1.0 --to {version_list[-1].get('version', '1.0')}[/hint]"
    )
    console.print()


@app.command()
def diff(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    from_version: str = typer.Option(..., "--from", help="From version"),
    to_version: str = typer.Option(..., "--to", help="To version"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Show metadata diff between two versions.

    Examples:
        mdf dataset diff my_dataset_v1 --from 1.0 --to 2.0
    """
    resolved = resolve_service(service)
    client = read_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user)
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Loading diff..."):
        result = client.version_diff(source_id, from_version, to_version)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
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
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Edit metadata on a submission.

    Examples:
        mdf dataset edit my_dataset_v1 --title "New Title"
        mdf dataset edit my_dataset_v1 --keyword "XRD" --keyword "diffraction"
    """
    resolved = resolve_service(service)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    fields = {}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if keywords is not None:
        fields["keywords"] = keywords

    if not fields:
        console.print(theme.error_panel(
            "No fields provided to edit.",
            title="Nothing to edit",
            hint="use --title, --description, or --keyword",
        ))
        raise typer.Exit(code=1)

    with api_spinner("Editing metadata..."):
        result = client.edit_metadata(source_id, version=version, **fields)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
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
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Withdraw a pending_curation submission.

    Examples:
        mdf dataset withdraw my_dataset_v1
        mdf dataset withdraw my_dataset_v1 --reason "Duplicate submission"
    """
    import sys
    resolved = resolve_service(service)
    if not yes and sys.stdin.isatty():
        if not typer.confirm(f"Withdraw {source_id}? This removes it from the curation queue.", default=False):
            raise typer.Exit(code=1)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Withdrawing..."):
        result = client.withdraw(source_id, reason=reason or "", version=version)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
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
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Resubmit a rejected dataset back to the curation queue.

    Examples:
        mdf dataset resubmit my_dataset_v1 --notes "Fixed title and added authors"
    """
    resolved = resolve_service(service)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Resubmitting..."):
        result = client.resubmit(source_id, notes=notes or "", version=version)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
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
