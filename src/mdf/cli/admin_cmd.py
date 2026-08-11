"""Admin and curation subcommands: pending, approve, reject, delete, stats."""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from rich.markup import escape

from mdf.core.backend_client import BackendClient
from mdf.core.config import resolve_service
from mdf.cli import theme
from mdf.cli.formatting import (
    api_spinner,
    console,
    format_status_badge,
    handle_api_result,
    is_success,
    require_success,
)
from mdf.cli.preflight import resolve_dataset_identifier

app = typer.Typer(
    help="Curation and admin commands",
    no_args_is_help=True,
)


def _resolve_identifier_with_notice(client: BackendClient, raw_id: str) -> str:
    resolved_id = resolve_dataset_identifier(raw_id, client)
    if resolved_id != raw_id:
        console.print(
            f"[secondary]Resolved DOI to source ID:[/secondary] {theme.ident(resolved_id)}"
        )
    return resolved_id


@app.command()
def pending(
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    organization: Optional[str] = typer.Option(None, "--organization", "-o", help="Filter by organization"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """List datasets awaiting curation.

    Examples:
        mdf admin pending
        mdf admin pending --organization argonne
    """
    resolved = resolve_service(service)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    with api_spinner("Loading pending..."):
        result = client.curation_pending(limit=limit, organization=organization)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        return

    submissions = result.get("submissions", [])
    if not submissions and not is_success(result):
        # Distinguish "the queue is empty" from "we could not read the queue".
        # (Exit code stays 0, as it always has for this command.)
        handle_api_result(result, error_prefix="Pending failed")
        return
    if not submissions:
        console.print()
        console.print(theme.empty_panel(
            "No datasets pending curation.",
            title="queue empty",
            hints=["nothing to review right now — check `mdf admin stats` for totals"],
        ))
        return

    console.print(
        f"\n[bold]Pending curation[/bold] [secondary]({len(submissions)})[/secondary]\n"
    )
    table = theme.table()
    table.add_column("#", style="secondary", width=3, justify="right")
    table.add_column("Source ID", no_wrap=True, style="id")
    table.add_column("Title", max_width=40)
    table.add_column("Version", style="secondary", no_wrap=True, justify="right")
    table.add_column("Submitted", style="secondary", no_wrap=True)

    for i, sub in enumerate(submissions, 1):
        table.add_row(
            str(i),
            escape(str(sub.get("source_id", ""))),
            escape(str(sub.get("title") or "Untitled")),
            escape(str(sub.get("version", ""))),
            escape(str(sub.get("submitted_at") or sub.get("created_at") or "")[:19]),
        )
    console.print(table)


@app.command()
def approve(
    source_id: str = typer.Argument(..., help="Source ID of dataset to approve"),
    mint_doi: bool = typer.Option(True, "--mint-doi/--no-mint-doi", help="Mint a DOI"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="Curator notes"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Approve a dataset for publication.

    Examples:
        mdf admin approve my_dataset_v1
        mdf admin approve my_dataset_v1 --notes "LGTM" --no-mint-doi
    """
    resolved = resolve_service(service)
    if not yes and sys.stdin.isatty():
        if not typer.confirm(f"Approve {source_id}? This will start publication.", default=False):
            raise typer.Exit(code=1)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Approving..."):
        result = client.curation_approve(
            source_id=source_id,
            mint_doi=mint_doi,
            notes=notes,
            version=version,
        )
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
        return

    if is_success(result):
        detail = theme.detail_table(label_width=8)
        detail.add_row("Dataset", theme.ident(source_id))
        detail.add_row("Status", format_status_badge("approved"))
        if result.get("doi"):
            detail.add_row("DOI", f"https://doi.org/{escape(str(result.get('doi')))}")
        console.print()
        console.print(theme.success_panel(
            detail, title="Approved", subtitle="publication started"
        ))
    else:
        require_success(result, error_prefix="Approve failed")


@app.command()
def reject(
    source_id: str = typer.Argument(..., help="Source ID of dataset to reject"),
    reason: str = typer.Option(..., "--reason", "-r", help="Rejection reason"),
    suggestions: Optional[str] = typer.Option(None, "--suggestions", help="Suggestions for improvement"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Reject a dataset and return to submitter.

    Examples:
        mdf admin reject my_dataset_v1 --reason "Missing methods section"
    """
    resolved = resolve_service(service)
    if not yes and sys.stdin.isatty():
        if not typer.confirm(f"Reject {source_id}? This will return the submission to the author.", default=False):
            raise typer.Exit(code=1)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Rejecting..."):
        result = client.curation_reject(
            source_id=source_id,
            reason=reason,
            suggestions=suggestions,
            version=version,
        )
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
        return

    if is_success(result):
        console.print()
        console.print(theme.panel(
            f"{theme.ident(source_id)} was returned to its submitter.\n"
            f"[label]Status:[/label] {format_status_badge('rejected')}\n"
            f"[label]Reason:[/label] {escape(str(reason))}",
            title=f"{theme.FAIL} Rejected",
            border="yellow",
            title_style="warn",
        ))
    else:
        require_success(result, error_prefix="Reject failed")


@app.command("delete")
def delete_dataset(
    source_id: str = typer.Argument(..., help="Source ID of dataset to delete"),
    reason: str = typer.Option(..., "--reason", "-r", help="Deletion reason (required)"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Target version"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Soft-delete a submission (curator-only).

    Examples:
        mdf admin delete my_dataset_v1 --reason "spam"
    """
    resolved = resolve_service(service)
    if not yes and sys.stdin.isatty():
        if not typer.confirm(f"Delete {source_id}? This will soft-delete the submission.", default=False):
            raise typer.Exit(code=1)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Deleting..."):
        result = client.delete_submission(source_id, reason=reason, version=version)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
        return

    if is_success(result):
        console.print()
        console.print(theme.panel(
            f"{theme.ident(source_id)} was soft-deleted.\n"
            f"[label]Status:[/label] {format_status_badge('deleted')}\n"
            f"[label]Reason:[/label] {escape(str(reason))}",
            title=f"{theme.FAIL} Deleted",
            border="red",
            title_style="err",
        ))
    else:
        require_success(result, error_prefix="Delete failed")


@app.command()
def stats(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Show admin statistics (curator-only).

    Examples:
        mdf admin stats
        mdf admin stats --json
    """
    resolved = resolve_service(service)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    with api_spinner("Loading stats..."):
        result = client.admin_stats()
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
        return

    if not is_success(result):
        require_success(result, error_prefix="Stats failed")

    console.print()
    console.print(theme.rule("Admin statistics"))
    console.print(
        f"\n[label]Total submissions:[/label] {theme.number(result.get('total', 0))}\n"
    )

    by_status = result.get("by_status", {})
    if by_status:
        table = theme.table()
        table.add_column("Status", no_wrap=True)
        table.add_column("Count", justify="right", no_wrap=True, style="value")
        for st, count in sorted(by_status.items()):
            table.add_row(format_status_badge(st), str(count))
        console.print(table)

    access = result.get("access_totals", {})
    if access:
        console.print(
            f"\n[label]Total views:[/label] {theme.number(access.get('view_count', 0))}"
        )
        console.print(
            f"[label]Total downloads:[/label] {theme.number(access.get('download_count', 0))}"
        )

    console.print()


@app.command("embedding-status")
def embedding_status(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Show embedding coverage and snapshot info (curator-only).

    Examples:
        mdf admin embedding-status
    """
    resolved = resolve_service(service)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    with api_spinner("Loading embedding status..."):
        result = client.embedding_status()
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
        return

    if not is_success(result):
        require_success(result, error_prefix="Embedding status failed")

    console.print()
    console.print(theme.rule("Embedding coverage"))
    coverage = theme.detail_table(label_width=16)
    coverage.add_row("Current model", escape(str(result.get("current_model"))))
    coverage.add_row("Published", theme.number(result.get("published_total", 0)))
    coverage.add_row("With embedding", theme.number(result.get("with_embedding", 0)))
    coverage.add_row("Missing", theme.number(result.get("without_embedding", 0)))
    stale = result.get("stale") or 0
    if stale:
        coverage.add_row(
            "Stale",
            f"{theme.number(stale)} [warn](metadata edited after embedding)[/warn]",
        )
    for model, count in (result.get("by_model") or {}).items():
        coverage.add_row(f"  {model}", theme.number(count))
    console.print(coverage)

    snapshot = result.get("snapshot")
    if snapshot:
        snap = theme.detail_table(label_width=16)
        snap.add_row("Built at", escape(str(snapshot.get("built_at"))))
        snap.add_row(
            "Count / dims",
            f"{theme.number(snapshot.get('count'))} / {theme.number(snapshot.get('dims'))}",
        )
        snap.add_row("Primary model", escape(str(snapshot.get("model"))))
        snap.add_row("sha", escape(str(snapshot.get("sha"))))
        console.print()
        console.print(theme.panel(snap, title="Current snapshot"))
    else:
        console.print()
        console.print(theme.empty_panel(
            "No snapshot built yet.",
            title="no snapshot",
            hints=["run `mdf admin rebuild-embeddings` to create one"],
        ))
    console.print()


@app.command("rebuild-embeddings")
def rebuild_embeddings(
    force: bool = typer.Option(
        False, "--force", help="Re-embed records that already have a vector"
    ),
    no_snapshot: bool = typer.Option(
        False, "--no-snapshot", help="Skip rebuilding the S3 snapshot"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Max records to embed in this run"
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Generate missing dataset embeddings and rebuild the semantic-search snapshot.

    Examples:
        mdf admin rebuild-embeddings
        mdf admin rebuild-embeddings --force --limit 500
        mdf admin rebuild-embeddings --no-snapshot   # fill embeddings, skip S3 publish
    """
    if not yes and sys.stdin.isatty():
        msg = "Rebuild embeddings for all published datasets lacking them?"
        if force:
            msg = "Re-embed ALL published datasets (--force)? This calls OpenAI for each."
        if not typer.confirm(msg, default=False):
            raise typer.Exit(code=1)

    resolved = resolve_service(service)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    with api_spinner("Enqueueing embedding jobs..."):
        result = client.rebuild_embeddings(
            force=force, build_snapshot=not no_snapshot, limit=limit
        )
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        if not is_success(result):
            raise typer.Exit(code=1)
        return

    if not is_success(result):
        require_success(result, error_prefix="Rebuild failed")

    detail = theme.detail_table(label_width=21)
    detail.add_row("Model", escape(str(result.get("model"))))
    if result.get("force"):
        detail.add_row("Mode", "force (re-embed everything)")
    if result.get("limit"):
        detail.add_row("Limit", theme.number(result.get("limit")))
    detail.add_row(
        "Build snapshot after",
        "yes" if result.get("build_snapshot", True) else "no",
    )

    dispatch = result.get("dispatch_job") or {}
    if dispatch.get("message_id"):
        detail.add_row("SQS message id", theme.ident(dispatch["message_id"]))
    elif dispatch.get("job_id") is not None:
        detail.add_row("Job id", theme.ident(dispatch["job_id"]))

    console.print()
    console.print(theme.success_panel(detail, title="Rebuild dispatched"))
    console.print(
        "[hint]Scan + per-dataset fan-out runs in the async worker. "
        "Poll: mdf admin embedding-status[/hint]"
    )
    console.print()
