"""MDF Agent CLI - Main entry point.

This module provides the command-line interface for MDF Agent.
Commands are organized as direct subcommands of the main `mdf` command.

Usage:
    mdf init --title "My Dataset" --author "Jane Doe"
    mdf publish --submit
    mdf publish ./data/ --title "Test" --author "Jane" --submit
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import typer.rich_utils as typer_rich_utils
from rich import box as rich_box
from rich.markup import escape
from rich.panel import Panel as RichPanel

from mdf.version import __version__
from mdf.core.agent import MDFAgent
from mdf.core.backend_client import BackendClient, _api_url_for_service
from mdf.core.config import GlobalConfig, resolve_service
from mdf.cli import theme
from mdf.cli.formatting import (
    api_spinner,
    classify_api_error,
    console,
    format_status_badge,
    is_success,
    read_client,
    require_success,
)
from mdf.cli.backend import app as backend_app
from mdf.cli.stream import app as stream_app
from mdf.cli.config_cmd import app as config_app
from mdf.cli.dataset_cmd import app as dataset_app
from mdf.cli.admin_cmd import app as admin_app
from mdf.cli.skill_cmd import app as skill_app
from mdf.cli.preflight import (
    auth_ready,
    resolve_dataset_identifier,
    run_preflight,
    suggest_data_sources,
)


app = typer.Typer(
    help="MDF Agent — Materials Data Facility",
    add_completion=True,
    invoke_without_command=True,
)

#                            11 visible chars prefix then letters
#                            "···········"
# The wordmark art carries the brand accent (magenta); the starfield stays
# multi-colored, and dataset identifiers elsewhere keep cyan to themselves.
_LOGO = (
    "\n"
    "  [dim].[/dim]    [green]·[/green]        [blue].[/blue]    [dim]·[/dim]    [green]*[/green]          [blue].[/blue]       [dim]·[/dim]        [green].[/green]    [blue]*[/blue]    [dim].[/dim]\n"
    "       [blue]*[/blue]       [dim].[/dim]          [green]·[/green]       [blue].[/blue]            [dim]*[/dim]        [green].[/green]\n"
    "  [green]·[/green]         [brand]__  __  ____   _____[/brand]         [blue]·[/blue]          [dim].[/dim]       [green]*[/green]\n"
    "      [blue].[/blue]    [brand]|  \\/  ||  _ \\ |  ___|[/brand]   [green]*[/green]       [dim].[/dim]              [blue]·[/blue]\n"
    "  [dim]*[/dim]        [brand]| |\\/| || | | || |_[/brand]             [blue].[/blue]     [green]·[/green]        [dim].[/dim]\n"
    "       [green]·[/green]   [brand]| |  | || |_| ||  _|[/brand]    [dim].[/dim]          [blue]*[/blue]          [green].[/green]\n"
    "  [blue].[/blue]        [brand]|_|  |_||____/ |_|[/brand]            [green].[/green]       [dim]·[/dim]         [blue].[/blue]\n"
    "      [dim]*[/dim]          [green]·[/green]      [blue].[/blue]       [dim]·[/dim]     [green].[/green]        [blue]*[/blue]     [dim].[/dim]      [green]·[/green]\n"
    "  [green].[/green]        [bold blue]Materials[/bold blue] [bold green]Data[/bold green] [brand]Facility[/brand]        [dim]·[/dim]          [blue].[/blue]\n"
    "      [blue]·[/blue]    [dim].[/dim]       [green]*[/green]        [blue].[/blue]    [dim]·[/dim]    [green].[/green]       [blue]*[/blue]    [dim].[/dim]       [green]·[/green]\n"
)

PANEL_PUBLISH = "Publish & Download"
PANEL_EXPLORE = "Explore"
PANEL_AUTH = "Auth & Setup"
PANEL_MORE = "More"


def _help_panel(*args, **kwargs):
    kwargs.setdefault("box", rich_box.SIMPLE)
    return RichPanel(*args, **kwargs)


typer_rich_utils.Panel = _help_panel

# Help output is the CLI's front door: give it the same accent as everything else.
theme.style_typer_help()

# Visible sub-apps
app.add_typer(dataset_app, name="dataset", rich_help_panel=PANEL_MORE)
app.add_typer(admin_app, name="admin", rich_help_panel=PANEL_MORE)
app.add_typer(config_app, name="config", rich_help_panel=PANEL_MORE)
app.add_typer(skill_app, name="skill", rich_help_panel=PANEL_MORE)

# Hidden sub-apps
app.add_typer(backend_app, name="backend", hidden=True)
app.add_typer(stream_app, name="stream", hidden=True)


# ---------------------------------------------------------------------------
# Main callback — first-run welcome or help
# ---------------------------------------------------------------------------

def _version_callback(value: bool) -> None:
    if value:
        console.print(f"mdf {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version and exit",
        callback=_version_callback, is_eager=True,
    ),
):
    """MDF Agent — Materials Data Facility"""
    if ctx.invoked_subcommand is not None:
        return

    # First-run: no config file yet
    cfg_path = Path.home() / ".config" / "mdf" / "config.json"
    if not cfg_path.exists():
        console.print(_LOGO)
        console.print(theme.panel(
            "[secondary]Publish, find and reuse materials data.[/secondary]\n"
            "\n"
            "[bold]Get started[/bold]\n"
            "  [id]mdf setup[/id]                 Configure defaults and create a manifest\n"
            "  [id]mdf publish --submit[/id]      Publish to MDF Connect\n"
            '  [id]mdf search "perovskite"[/id]   Find datasets\n'
            "  [id]mdf login[/id]                 Authenticate with Globus\n"
            "\n"
            "[secondary]For scripts/CI: set[/secondary] [id]MDF_CLIENT_ID[/id] [secondary]+[/secondary] [id]MDF_CLIENT_SECRET[/id]\n"
            "\n"
            "[hint]hint: run `mdf --help` for all commands[/hint]",
            title=f"{theme.SCIENCE} MDF Agent",
            subtitle=f"v{__version__}",
        ))
    else:
        # Show help
        console.print(_LOGO)
        console.print(ctx.get_help())


# ---------------------------------------------------------------------------
# Shared CLI helpers
# ---------------------------------------------------------------------------


#: preflight severity -> checklist glyph state. A warning reads as "needs your
#: attention" (yellow ●) rather than an outright failure; notes are dim ○.
_PREFLIGHT_STATES = {"blocking": "err", "warning": "active"}


def _render_preflight(preflight: Dict[str, Any], title: str = "Preflight") -> None:
    """Render preflight as a pre-launch checklist: derived checks, then issues."""
    summary = preflight.get("source_summary") or {}
    issues = preflight.get("issues") or []
    sources = preflight.get("sources") or []

    blocking = [issue for issue in issues if issue.get("severity") == "blocking"]
    files = int(summary.get("files") or 0)
    total_bytes = int(summary.get("bytes") or 0)
    source_count = int(summary.get("count") or len(sources))
    service = preflight.get("service") or "unknown"
    target = preflight.get("target_url") or "default service URL"

    console.print()
    console.print(theme.rule(title))
    console.print()

    checks = theme.checklist_table()
    theme.add_check(
        checks,
        "err" if blocking else "ok",
        "Manifest checks",
        detail=(
            f"{len(blocking)} blocking issue(s)"
            if blocking
            else "metadata and paths look publishable"
        ),
    )
    theme.add_check(
        checks,
        "ok" if files else "idle",
        "Data staged",
        detail=(
            f"{source_count} source(s) · {files} file(s) · {_human_size(total_bytes)}"
            if files
            else "no files resolved yet"
        ),
    )
    theme.add_check(
        checks,
        "ok",
        "Destination",
        detail=f"{escape(str(service))} · {escape(str(target))}",
    )
    console.print(checks)

    if sources:
        console.print()
        table = theme.table()
        table.add_column("Source", style="id")
        table.add_column("Kind", style="secondary")
        table.add_column("Files", justify="right", style="value")
        table.add_column("Size", justify="right", style="value")
        for source in sources:
            file_count = source.get("file_count")
            bytes_value = source.get("total_bytes")
            table.add_row(
                escape(str(source.get("source", ""))),
                escape(str(source.get("kind", ""))),
                "-" if file_count is None else str(file_count),
                "-" if bytes_value is None else _human_size(bytes_value),
            )
        console.print(table)

    if issues:
        console.print()
        findings = theme.checklist_table(detail_column=False)
        for issue in issues:
            theme.add_check(
                findings,
                _PREFLIGHT_STATES.get(issue.get("severity", "info"), "idle"),
                escape(str(issue.get("message", ""))),
                hint=escape(str(issue["hint"])) if issue.get("hint") else None,
            )
        console.print(findings)
    else:
        console.print()
        console.print(theme.check_line("ok", "No preflight issues detected.", indent=0))


def _resolve_identifier_with_notice(client: BackendClient, raw_id: str) -> str:
    resolved_id = resolve_dataset_identifier(raw_id, client)
    if resolved_id != raw_id:
        console.print(
            f"[secondary]Resolved DOI to source ID:[/secondary] {theme.ident(resolved_id)}"
        )
    return resolved_id


def _extract_feedback_fields(record: Dict[str, Any]) -> List[tuple[str, str]]:
    feedback: List[tuple[str, str]] = []
    direct_candidates = [
        ("Reason", record.get("rejection_reason") or record.get("reason")),
        ("Suggestions", record.get("suggestions")),
        ("Curator notes", record.get("notes")),
    ]
    for label, value in direct_candidates:
        if value:
            feedback.append((label, str(value)))

    history = record.get("curation_history")
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except Exception:
            history = []
    if isinstance(history, list):
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            for label, key in (("Reason", "reason"), ("Suggestions", "suggestions"), ("Curator notes", "notes")):
                value = item.get(key)
                if value and (label, str(value)) not in feedback:
                    feedback.append((label, str(value)))
            if feedback:
                break
    return feedback


def _render_status_details(result: Dict[str, Any], lookup_id: str) -> None:
    sub = result.get("submission") or result
    sid = sub.get("source_id")
    if sid:
        ver = sub.get("version", "?")
        st = sub.get("status", "unknown")
        console.print(
            f"\n[bold]Backend status:[/bold] {theme.ident(sid)} [secondary]v{escape(str(ver))}[/secondary]"
        )
        console.print(f"  [label]Status:[/label] {format_status_badge(st)}")
        mdata = sub.get("dataset_mdata", {})
        if isinstance(mdata, str):
            try:
                mdata = json.loads(mdata)
            except Exception:
                mdata = {}
        if isinstance(mdata, dict):
            if mdata.get("title"):
                console.print(f"  [label]Title:[/label] {escape(str(mdata['title']))}")
        if sub.get("dataset_doi") or sub.get("doi"):
            doi = sub.get("dataset_doi") or sub.get("doi")
            console.print(f"  [label]DOI:[/label] https://doi.org/{escape(str(doi))}")

        transfer = result.get("transfer", {})
        if transfer and transfer.get("status"):
            t_status = transfer["status"]
            t_bytes = transfer.get("bytes_transferred", 0)
            t_files = transfer.get("files_transferred", 0)
            if t_status == "active":
                console.print(
                    f"  [label]Transfer:[/label] {theme.mark('active', 'active')} "
                    f"[secondary]—[/secondary] {theme.number(t_files)} files, {theme.number(f'{t_bytes:,}')} bytes transferred"
                )
            elif t_status == "succeeded":
                console.print(
                    f"  [label]Transfer:[/label] {theme.mark('ok', 'complete', style='ok')} "
                    f"[secondary]—[/secondary] {theme.number(t_files)} files, {theme.number(f'{t_bytes:,}')} bytes"
                )
            elif t_status == "failed":
                console.print(f"  [label]Transfer:[/label] {theme.mark('err', 'failed')}")

        if isinstance(mdata, dict):
            is_latest = mdata.get("latest", True)
            root_version = mdata.get("root_version", "")
            prev_version = mdata.get("previous_version", "")
            if prev_version:
                console.print(f"  [label]Previous version:[/label] {escape(str(prev_version))}")
            if root_version and root_version != f"{sid}-{ver}":
                console.print(f"  [label]Root version:[/label] {escape(str(root_version))}")
            if not is_latest:
                console.print(f"  {theme.mark('active', 'This is not the latest version', style='warn')}")

        for label, value in _extract_feedback_fields(sub):
            console.print(f"  [label]{label}:[/label] {escape(str(value))}")

        if st == "pending_curation":
            console.print("  [label]Next:[/label] Waiting for curation review")
            console.print(f"  [hint]Try: mdf status --watch {sid}[/hint]")
        elif st == "approved":
            console.print("  [label]Next:[/label] Publishing in progress...")
            console.print(f"  [hint]Try: mdf status --watch {sid}[/hint]")
        elif st == "published":
            console.print("  [label]Next:[/label] Dataset is live!")
            console.print(f"  [hint]Try: mdf show {sid} | mdf dataset cite {sid} | mdf dataset open {sid}[/hint]")
        elif st == "publish_failed":
            console.print("  [warn]Next:[/warn] Approved, but the search ingest failed — not yet live. The pipeline retries; if it persists, contact a curator.")
            console.print(f"  [hint]Try: mdf status --watch {sid}[/hint]")
        elif st == "rejected":
            console.print("  [label]Next:[/label] Review feedback, edit metadata if needed, then resubmit")
            console.print(f"  [hint]Try: mdf dataset edit {sid} --title \"Updated title\" | mdf dataset resubmit {sid}[/hint]")
        elif st == "withdrawn":
            console.print("  [label]Next:[/label] Update the submission, then resubmit if appropriate")
        elif st == "deleted":
            console.print("  [label]Next:[/label] This record is no longer active")
    elif result.get("error"):
        console.print()
        console.print(
            theme.error_panel(
                escape(str(result.get("error"))),
                title="Backend",
                border="yellow",
                hint=f"confirm the identifier with `mdf list --search {lookup_id}`",
            )
        )
    else:
        console.print()
        console.print(
            theme.empty_panel(
                f"No backend record for {escape(str(lookup_id))}",
                title="no record",
                hints=[
                    "list your submissions with `mdf list`",
                    "search public datasets with `mdf search <query>`",
                ],
            )
        )


def _watch_submission(
    lookup_id: str,
    interval: int,
    timeout: int,
    service: str,
    api_url: Optional[str],
    token: Optional[str],
    dev_user: Optional[str],
    json_output: bool = False,
    announce: bool = True,
    stop_on_statuses: Optional[set[str]] = None,
) -> int:
    terminal_states = {"published", "publish_failed", "rejected", "failed", "deleted", "withdrawn"}
    start = time.monotonic()

    if announce and not json_output:
        console.print(
            f"\n[bold]Watching[/bold] {theme.ident(lookup_id)} "
            f"[secondary](poll every {interval}s, timeout {timeout}s)[/secondary]\n"
        )

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            if json_output:
                print(json.dumps({"source_id": lookup_id, "status": "timeout", "elapsed": round(elapsed)}))
            else:
                console.print()
                console.print(
                    theme.error_panel(
                        f"Timeout after {timeout}s",
                        title="Watch stopped",
                        border="yellow",
                        detail="the submission is still in the queue; watching just gave up waiting.",
                        hint=f"check again with `mdf status {lookup_id}`",
                    )
                )
            return 1

        try:
            client = BackendClient.authenticated(
                base_url=api_url,
                token=token,
                service_instance=service,
                dev_user_id=dev_user,
            )
            result = client.status(lookup_id)
            client.close()
        except Exception as exc:
            if json_output:
                print(json.dumps({"source_id": lookup_id, "error": str(exc), "elapsed": round(elapsed)}))
            else:
                console.print(
                    f"  {theme.mark('active', 'Error:', style='warn')} {escape(str(exc))} "
                    f"[hint](retrying in {interval}s)[/hint]"
                )
            time.sleep(interval)
            continue

        sub = result.get("submission") or result
        st = sub.get("status", "unknown")

        if json_output:
            print(json.dumps({"source_id": lookup_id, "status": st, "elapsed": round(elapsed)}))
        else:
            console.print(
                f"  {format_status_badge(st)}  [secondary]({round(elapsed)}s)[/secondary]"
            )

        if stop_on_statuses and st in stop_on_statuses:
            if not json_output:
                console.print()
                _render_status_details(result, lookup_id)
            return 0

        if st in terminal_states:
            if json_output:
                return 0 if st == "published" else 1
            console.print()
            _render_status_details(result, lookup_id)
            if st == "published":
                console.print(f"\n[ok.bold]{theme.SPARKLE} Done![/ok.bold]")
                return 0
            console.print(f"\n{theme.mark('err', f'Terminal state: {escape(str(st))}')}")
            return 1

        time.sleep(interval)


def _render_identity(
    *,
    service: str,
    status_str: str,
    token_store: str,
    method: Optional[str] = None,
    env_token: bool = False,
    env_client: bool = False,
) -> None:
    """The identity panel used by `mdf status --auth` and `mdf whoami`."""
    authenticated = status_str == "authenticated"
    detail = theme.detail_table(label_width=12)
    detail.add_row("Service", theme.ident(service))
    detail.add_row(
        "Status",
        theme.mark("ok" if authenticated else "idle", escape(status_str), style="ok" if authenticated else "secondary"),
    )
    if method:
        detail.add_row("Method", escape(str(method)))
    detail.add_row("Token store", escape(str(token_store)))

    notes: List[str] = []
    if env_token:
        notes.append("MDF_CONNECT_TOKEN is set in environment")
    if env_client:
        notes.append("MDF_CLIENT_ID + MDF_CLIENT_SECRET are set in environment")
    if not authenticated:
        notes.append(f"hint: run `mdf login --service {service}`")

    body: Any = detail
    if notes:
        from rich.console import Group as _Group

        body = _Group(detail, "\n" + "\n".join(f"[hint]{escape(note)}[/hint]" for note in notes))

    console.print()
    console.print(theme.panel(
        body,
        title=f"{theme.OK if authenticated else theme.IDLE} Identity",
        subtitle=service,
        title_style="ok" if authenticated else "secondary",
        border="ok" if authenticated else "dim",
    ))


def _maybe_confirm(action: str, target: str, yes: bool = False, detail: Optional[str] = None) -> None:
    if yes or not sys.stdin.isatty():
        return
    message = f"{action} {target}?"
    if detail:
        message = f"{message} {detail}"
    if not typer.confirm(message, default=False):
        raise typer.Exit(code=1)


#: clone stage -> human label for the transfer progress headline.
_CLONE_STAGE_LABELS = {
    "resolving_dataset": "Resolving dataset",
    "downloading_archive": "Downloading archive",
    "extracting_archive": "Extracting archive",
    "downloading_files": "Downloading files",
    "queueing_transfer": "Queueing Globus Transfer",
    "transfer_queued": "Transfer queued",
    "falling_back_to_https": "Archive unavailable, switching to direct file download",
}


def _clone_method_label(method: str) -> str:
    return {
        "zip": "Archive download",
        "https": "Direct file download",
        "transfer": "Globus Transfer",
    }.get(method, method)


def _render_clone_plan(plan: Dict[str, Any]) -> None:
    detail = theme.detail_table(label_width=13)
    detail.add_row("Dataset", theme.ident(plan.get("resolved_source_id")))
    detail.add_row("Title", escape(str(plan.get("title") or "Untitled")))
    detail.add_row("Destination", escape(str(plan.get("output_path"))))
    detail.add_row("Method", escape(_clone_method_label(plan.get("selected_method", ""))))
    if plan.get("version"):
        detail.add_row("Version", escape(str(plan["version"])))
    if plan.get("requested_identifier") != plan.get("resolved_source_id"):
        detail.add_row("Requested", escape(str(plan.get("requested_identifier"))))
    if plan.get("archive_available") and plan.get("archive_size"):
        detail.add_row("Archive", theme.number(_human_size(plan["archive_size"])))
    elif plan.get("file_count"):
        detail.add_row("Files", theme.number(plan["file_count"]))
    console.print()
    console.print(theme.panel(detail, title="Clone plan"))


def _render_clone_result(result: Dict[str, Any], derive: bool = False) -> None:
    if result.get("success"):
        queued_transfer = result.get("method") == "transfer" and result.get("task_id")
        heading = "Transfer queued!" if queued_transfer else "Clone complete!"
        detail = theme.detail_table(label_width=14)
        if result.get("title"):
            detail.add_row("Title", escape(str(result["title"])))
        detail.add_row("Method", escape(_clone_method_label(result.get("method", ""))))
        if result.get("files_count") is not None:
            detail.add_row("Files", theme.number(result["files_count"]))
        detail.add_row("Path", escape(str(result["path"])))
        if queued_transfer:
            detail.add_row("Status", "Waiting on Globus Transfer to finish")
            detail.add_row("Transfer task", theme.ident(result["task_id"]))
            detail.add_row("Monitor", escape(str(result["monitor_url"])))
        if derive:
            detail.add_row("Lineage", theme.mark("ok", "mdf.yaml written with derived-from", style="ok"))

        source_id = result.get("resolved_source_id") or result.get("source_id")
        body: Any = detail
        if not queued_transfer and source_id:
            from rich.console import Group as _Group

            body = _Group(
                detail,
                f"\n[hint]Next: cd {escape(str(result['path']))} | mdf show {escape(str(source_id))}[/hint]",
            )
        console.print()
        console.print(theme.success_panel(body, title=heading, emoji=theme.SPARKLE))
        console.print()
        return

    downloaded = result.get("files_count", 0)
    failed = result.get("failed_count")
    if result.get("partial") and result.get("errors"):
        lines = [f"Downloaded {theme.number(downloaded)} file(s)."]
        if failed is not None:
            lines.append(f"Failed {theme.number(failed)} file(s).")
        lines.append(f"[label]Path:[/label] {escape(str(result.get('path')))}")
        lines.append("")
        lines.append("[bold]Failed files[/bold]")
        for err in result.get("errors", []):
            lines.append(f"  {theme.mark('err', escape(str(err)))}")
        lines.append("")
        lines.append(
            "[hint]hint: re-run `mdf clone ...` into the same destination or a clean directory[/hint]"
        )
        console.print()
        console.print(
            theme.panel(
                "\n".join(lines),
                title=f"{theme.ACTIVE} Clone partially completed",
                border="yellow",
                title_style="warn",
            )
        )
        console.print()
        raise typer.Exit(code=1)

    if result.get("error"):
        message = escape(str(result["error"]))
    elif result.get("errors"):
        message = "\n".join(theme.mark("err", escape(str(err))) for err in result["errors"])
    else:
        message = "The dataset could not be downloaded."
    console.print()
    console.print(
        theme.error_panel(
            message,
            title="Clone failed",
            hint="try `mdf clone <id> --transfer` for cross-endpoint / Globus-only datasets",
        )
    )
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# mdf setup — guided onboarding
# ---------------------------------------------------------------------------


@app.command(rich_help_panel=PANEL_AUTH)
def setup(
    path: str = typer.Argument(".", help="Dataset directory to initialize"),
):
    """Configure defaults and create a manifest."""
    from mdf.cli.manifest_cmd import manifest_init

    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    console.print()
    console.print(theme.rule(f"{theme.SCIENCE} MDF Agent setup  v{__version__}"))
    console.print()

    cfg = GlobalConfig()
    service_default = resolve_service(None)
    service = typer.prompt(
        "Default service",
        default=service_default,
        show_default=True,
    ).strip().lower()
    if service:
        cfg.set("defaults.service", service)

    organization = typer.prompt(
        "Organization (optional)",
        default=cfg.organization or "",
        show_default=bool(cfg.organization),
    ).strip()
    if organization:
        cfg.set("user.organization", organization)

    publisher = typer.prompt(
        "Publisher (optional)",
        default=cfg.publisher or "",
        show_default=bool(cfg.publisher),
    ).strip()
    if publisher:
        cfg.set("user.publisher", publisher)

    email_default = cfg.get("user.email") or ""
    email = typer.prompt(
        "Contact email (optional)",
        default=email_default,
        show_default=bool(email_default),
    ).strip()
    if email:
        cfg.set("user.email", email)

    console.print()
    authed = auth_ready(service)
    if authed:
        console.print(theme.check_line("ok", f"Auth ready for {escape(service)}", indent=0))
    else:
        console.print(
            theme.check_line(
                "idle",
                f"Not authenticated for {escape(service)}",
                hint=f"run `mdf login --service {escape(service)}`",
                indent=0,
            )
        )

    manifest_path = root / "mdf.yaml"
    if manifest_path.exists():
        console.print(
            theme.check_line("ok", "Found existing manifest:", detail=escape(str(manifest_path)), indent=0)
        )
    else:
        suggestions = suggest_data_sources(root)
        console.print(f"[label]Dataset directory:[/label] {escape(str(root))}")
        if suggestions:
            console.print(
                f"[label]Detected data candidates:[/label] {escape(', '.join(suggestions))}"
            )
        if typer.confirm("Create mdf.yaml in this directory?", default=True):
            manifest_title = typer.prompt(
                "Dataset title",
                default=root.name,
                show_default=True,
            ).strip()
            if publisher:
                manifest_author = typer.prompt(
                    "Author",
                    default=publisher,
                    show_default=True,
                ).strip()
            else:
                manifest_author = typer.prompt("Author").strip()
            manifest_description = typer.prompt(
                "Description (optional)",
                default="",
                show_default=False,
            ).strip()
            manifest_init(
                path=str(root),
                title=manifest_title,
                author=[manifest_author],
                description=manifest_description or None,
                publisher=publisher or None,
                publication_year=None,
                organization=organization or None,
                keyword=None,
            )

    steps: List[str] = []
    if not authed:
        steps.append(f"mdf login --service {service}")
    if manifest_path.exists() or (root / "mdf.yaml").exists():
        steps.extend([
            "mdf manifest inspect",
            "mdf publish --preflight-only",
            "mdf publish --submit",
        ])
    else:
        steps.append("mdf setup")

    console.print()
    console.print(theme.panel(
        "\n".join(f"[id]{escape(step)}[/id]" for step in steps),
        title="Next steps",
    ))


# ---------------------------------------------------------------------------
# Auth commands
# ---------------------------------------------------------------------------

@app.command(rich_help_panel=PANEL_AUTH)
def login(
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance (staging/prod/dev)"),
    token: Optional[str] = typer.Option(None, "--token", help="Use an explicit access token"),
):
    """Authenticate with Globus.

    For scripts and CI, you can skip interactive login entirely:

      MDF_CLIENT_ID + MDF_CLIENT_SECRET   Globus confidential client credentials
      MDF_CONNECT_TOKEN                   Pre-existing access token
    """
    from mdf.auth.globus import (
        DEFAULT_TOKEN_PATH,
        DATA_MDF_SCOPE,
        GROUPS_SCOPE,
        TRANSFER_SCOPE,
        get_authorizer_for_scopes,
        get_scopes_for_service,
    )

    resolved = resolve_service(service)

    def _reject_token(detail: str) -> None:
        console.print()
        console.print(theme.error_panel(
            f"Token validation failed for {escape(resolved)}",
            title="Login failed",
            detail=escape(str(detail)),
            hint="check the token, or run `mdf login` for an interactive Globus login",
        ))
        raise typer.Exit(code=1)

    if token:
        client = BackendClient.authenticated(token=token, service_instance=resolved)
        try:
            auth_result = client.auth_check()
        except Exception as exc:
            _reject_token(str(exc))
        finally:
            client.close()
        if (
            not auth_result
            or auth_result.get("success") is False
            or auth_result.get("authenticated") is False
            or auth_result.get("error")
            or auth_result.get("detail")
        ):
            error = auth_result.get("error") or auth_result.get("detail") or "token was rejected"
            _reject_token(str(error))
    else:
        scope, _rs = get_scopes_for_service(resolved)
        get_authorizer_for_scopes([scope, DATA_MDF_SCOPE, TRANSFER_SCOPE, GROUPS_SCOPE])

    detail = theme.detail_table(label_width=12)
    detail.add_row("Service", theme.ident(resolved))
    detail.add_row("Token store", escape(str(DEFAULT_TOKEN_PATH)))
    if token:
        detail.add_row("Source", "token from --token (this invocation only)")
    console.print()
    console.print(theme.success_panel(detail, title="Authentication ready"))


@app.command(rich_help_panel=PANEL_AUTH)
def logout():
    """Clear cached credentials."""
    from mdf.auth.globus import DEFAULT_TOKEN_PATH, logout as clear_cached_tokens

    removed = clear_cached_tokens()
    if removed:
        console.print(theme.mark("ok", "Logged out", style="ok"))
        console.print(f"[hint]cached Globus tokens removed from {escape(str(DEFAULT_TOKEN_PATH))}[/hint]")
    else:
        console.print(theme.check_line(
            "idle",
            "No cached token file found",
            detail=escape(str(DEFAULT_TOKEN_PATH)),
            hint="you were already logged out — run `mdf login` to authenticate",
            indent=0,
        ))


# ---------------------------------------------------------------------------
# Status — now absorbs whoami (--auth) and watch (--watch)
# ---------------------------------------------------------------------------

@app.command(rich_help_panel=PANEL_AUTH)
def status(
    source_id: Optional[str] = typer.Argument(None, help="Source ID to check (default: last published)"),
    auth: bool = typer.Option(False, "--auth", help="Show authentication/identity info"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Poll until terminal state"),
    interval: int = typer.Option(10, "--interval", "-i", help="Poll interval in seconds (with --watch)"),
    timeout: int = typer.Option(1800, "--timeout", help="Timeout in seconds (with --watch)"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Check submission or auth status.

    With source_id: shows backend status for that dataset.
    Without: shows backend status of last published dataset.
    With --auth: shows authentication/identity info.
    With --watch: polls until terminal state.
    """
    resolved = resolve_service(service)

    # --auth mode: show identity info (absorbs old whoami)
    if auth:
        from mdf.auth.globus import DEFAULT_TOKEN_PATH, is_logged_in

        cached = is_logged_in(service_instance=resolved)
        env_token = bool(os.environ.get("MDF_CONNECT_TOKEN"))
        env_client = bool(
            os.environ.get("MDF_CLIENT_ID") and os.environ.get("MDF_CLIENT_SECRET")
        )

        if env_client:
            method = "confidential client (MDF_CLIENT_ID)"
        elif env_token:
            method = "environment token (MDF_CONNECT_TOKEN)"
        elif cached:
            method = "cached Globus login"
        else:
            method = None

        status_str = "authenticated" if method else "not authenticated"

        result = {
            "success": True,
            "service": resolved,
            "status": status_str,
            "method": method,
            "token_store": str(DEFAULT_TOKEN_PATH),
            "env_token_set": env_token,
            "env_client_set": env_client,
        }

        if json_output:
            print(json.dumps(result, indent=2))
            return

        _render_identity(
            service=resolved,
            status_str=status_str,
            token_store=str(DEFAULT_TOKEN_PATH),
            method=method,
            env_token=env_token,
            env_client=env_client,
        )
        return

    lookup_id = source_id
    if not lookup_id:
        cfg = GlobalConfig()
        lookup_id = cfg.last_source_id
        if not lookup_id:
            console.print()
            console.print(theme.empty_panel(
                "No source_id provided and no last published dataset.",
                title="nothing to check",
                hints=[
                    "usage: mdf status <source_id>",
                    "list your submissions with `mdf list`",
                ],
            ))
            return

    # --watch mode: poll until terminal state (absorbs old watch)
    if watch:
        client = BackendClient.authenticated(
            base_url=api_url,
            token=token,
            service_instance=resolved,
            dev_user_id=dev_user,
        )
        lookup_id = _resolve_identifier_with_notice(client, lookup_id)
        client.close()

        raise typer.Exit(
            code=_watch_submission(
                lookup_id,
                interval=interval,
                timeout=timeout,
                service=resolved,
                api_url=api_url,
                token=token,
                dev_user=dev_user,
                json_output=json_output,
                announce=True,
            )
        )

    # Default: show status
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    lookup_id = _resolve_identifier_with_notice(client, lookup_id)
    with api_spinner("Checking status..."):
        result = client.status(lookup_id, version=version)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        return

    _render_status_details(result, lookup_id)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

@app.command(rich_help_panel=PANEL_PUBLISH)
def publish(
    data: Optional[List[str]] = typer.Argument(None, help="Data paths/URIs to publish"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Dataset title"),
    author: Optional[List[str]] = typer.Option(None, "--author", "-a", help="Author name (repeatable)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Dataset description"),
    dry_run: bool = typer.Option(True, "--dry-run/--submit", help="Preview without submitting"),
    test: bool = typer.Option(False, "--test", help="Submit to test environment"),
    update: bool = typer.Option(False, "--update", "-u", help="Update existing dataset"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    preflight_only: bool = typer.Option(False, "--preflight-only", help="Run submit-time checks without uploading"),
    no_watch: bool = typer.Option(False, "--no-watch", help="Do not watch after a successful submit"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance (staging/prod/dev/local)"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL for local backend"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id (X-User-Id)"),
):
    """Publish a dataset to MDF Connect.

    Three modes:

    1. Direct (data args provided):
        mdf publish ./data/ --title "My Dataset" --author "Jane" --submit

    2. Manifest (mdf.yaml in current directory):
        mdf publish --submit

    3. Manifest + overrides:
        mdf publish --title "New Title" --submit

    By default, performs a dry run showing the payload.
    Use --submit to actually send to MDF Connect.
    """
    from mdf.models.config import ManifestConfig

    resolved = resolve_service(service)

    manifest_path = Path.cwd() / "mdf.yaml"

    if data:
        for flag in ("--title", "--author"):
            if (flag == "--title" and not title) or (flag == "--author" and not author):
                console.print()
                console.print(theme.error_panel(
                    f"Direct mode requires {flag}",
                    title="Missing metadata",
                    hint='example: mdf publish ./data/ --title "My Dataset" --author "Jane"',
                ))
                raise typer.Exit(code=1)

        cfg = GlobalConfig()
        manifest = ManifestConfig(
            title=title,
            authors=author,
            description=description,
            data_sources=list(data),
            publisher=cfg.publisher,
            organization=cfg.organization,
        )
        agent = MDFAgent(root=None, manifest=manifest)

    elif manifest_path.exists():
        agent = MDFAgent.from_manifest(".")
        if title:
            agent.manifest.title = title
        if author:
            agent.manifest.authors = author
        if description:
            agent.manifest.description = description
        if not agent.manifest.data_sources:
            agent.manifest.data_sources = ["."]

    else:
        console.print()
        console.print(theme.error_panel(
            "No data paths provided and no mdf.yaml found",
            title="Nothing to publish",
            detail=(
                "Direct mode:    mdf publish ./data/ --title \"My Dataset\" --author \"Jane\" --submit\n"
                "Manifest mode:  mdf setup  →  mdf publish --submit"
            ),
            hint="run `mdf setup` to create an mdf.yaml in this directory",
        ))
        raise typer.Exit(code=1)

    preflight = run_preflight(
        agent.manifest,
        root=agent.root or Path.cwd(),
        service=resolved,
        api_url=api_url or _api_url_for_service(resolved),
        token=token,
        dev_user=dev_user,
        submit=preflight_only or not dry_run,
    )

    if preflight_only:
        if json_output:
            print(json.dumps(preflight, indent=2))
        else:
            _render_preflight(preflight)
        if not preflight.get("success"):
            raise typer.Exit(code=1)
        return

    if not preflight.get("success"):
        if json_output:
            print(json.dumps({"success": False, "preflight": preflight}, indent=2))
        else:
            _render_preflight(preflight)
            console.print()
            console.print(theme.error_panel(
                "Publish blocked by preflight issues",
                title="Blocked",
                hint="fix the ✗ items above, then re-run `mdf publish --preflight-only`",
            ))
        raise typer.Exit(code=1)

    payload = agent.build_submission(test=test, update=update)

    if dry_run:
        if json_output:
            print(json.dumps({"success": True, "dry_run": True, "payload": payload, "preflight": preflight}, indent=2))
            return
        _render_preflight(preflight, title="Publish preflight")
        target = api_url or _api_url_for_service(resolved)
        console.print()
        console.print(theme.rule("Dry run — would submit"))
        console.print(f"[label]Target:[/label] {escape(str(target))} [secondary]({escape(resolved)})[/secondary]")
        console.print()
        console.print(theme.json_syntax(payload))
        console.print()
        console.print(theme.panel(
            "[id]mdf publish --submit[/id]",
            title=f"{theme.PACKAGE} Ready to publish",
        ))
        return

    # Two-tier byte-accurate transfer UI (job + per-file), rendered on stderr.
    transfer = theme.TransferProgress("Uploading", done_description="Upload complete")

    try:
        with transfer:
            result = agent.publish(
                test=test,
                update=update,
                dry_run=False,
                token=token,
                service_instance=resolved,
                api_url=api_url,
                dev_user_id=dev_user,
                progress_callback=transfer.on_progress,
                on_files_resolved=transfer.on_files_resolved,
                on_file_done=transfer.on_file_done,
            )
    except RuntimeError as exc:
        console.print()
        console.print(theme.error_panel(
            escape(str(exc)),
            title="Upload failed",
            hint="check connectivity with `mdf config doctor`, then retry `mdf publish --submit`",
        ))
        raise typer.Exit(code=1)

    if json_output:
        print(json.dumps(result, indent=2))
        if not result.get("success"):
            raise typer.Exit(code=1)
        return

    if result.get("success"):
        source_id_val = result.get("source_id")
        version_val = result.get("version")
        detail = theme.detail_table(label_width=11)
        detail.add_row("Source ID", theme.ident(source_id_val))
        detail.add_row("Service", escape(str(resolved)))
        detail.add_row("Status", format_status_badge("pending_curation"))
        if version_val:
            detail.add_row("Version", theme.ident(version_val))
        console.print()
        console.print(theme.success_panel(
            detail,
            title="Published successfully!",
            emoji=theme.PACKAGE,
            subtitle="awaiting curation",
        ))

        if source_id_val:
            cfg = GlobalConfig()
            cfg.record_publish(source_id_val, version_val, resolved)
            console.print(f"[hint]Try: mdf status {source_id_val} | mdf show {source_id_val}[/hint]")
            if no_watch:
                console.print(f"[hint]Watch: mdf status --watch {source_id_val}[/hint]")
            else:
                raise typer.Exit(
                    code=_watch_submission(
                        source_id_val,
                        interval=5,
                        timeout=1800,
                        service=resolved,
                        api_url=api_url,
                        token=token,
                        dev_user=dev_user,
                        announce=True,
                        stop_on_statuses={"pending_curation"},
                    )
                )
    else:
        require_success(result, error_prefix="Publish failed")


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------

@app.command(rich_help_panel=PANEL_PUBLISH)
def clone(
    source_id: str = typer.Argument(..., help="Source dataset ID or DOI to download"),
    output_dir: Optional[str] = typer.Argument(None, help="Output directory (default: ./{source_id})"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Specific version to clone"),
    transfer: bool = typer.Option(False, "--transfer", help="Use Globus Transfer (requires GCP)"),
    derive: bool = typer.Option(False, "--derive", help="Create mdf.yaml with derived-from lineage"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API base URL"),
):
    """Download a dataset from MDF.

    Downloads using the fastest available method: zip archive (if available),
    HTTPS file-by-file, or Globus Transfer (with --transfer flag).

    Examples:
        mdf clone my_dataset_v1.1
        mdf clone my_dataset_v1.1 ./local_copy
        mdf clone my_dataset_v1.1 --transfer
        mdf clone my_dataset_v1.1 --derive
    """
    resolved = resolve_service(service)
    method = "transfer" if transfer else "auto"

    agent = MDFAgent()

    resolved_token = token or os.environ.get("MDF_CONNECT_TOKEN")
    resolved_dev_user = dev_user or os.environ.get("MDF_DEV_USER_ID")

    # Default output dir: ./{source_id} — use the last path component of a DOI
    if output_dir is None:
        dir_name = source_id.rstrip("/").split("/")[-1]
        output_dir = dir_name

    plan = agent.plan_clone(
        source_id=source_id,
        output_dir=output_dir,
        version=version,
        method=method,
        api_url=api_url,
        token=resolved_token,
        service_instance=resolved,
        dev_user_id=resolved_dev_user,
    )

    if json_output:
        if not plan.get("success"):
            print(json.dumps(plan, indent=2))
            raise typer.Exit(code=1)
    else:
        if not plan.get("success"):
            _render_clone_result(plan)
        headline = f"\n[bold]Cloning[/bold] {theme.ident(plan.get('resolved_source_id', source_id))}"
        if version:
            headline += f" [secondary]v{escape(str(version))}[/secondary]"
        console.print(headline)
        _render_clone_plan(plan)

    resolved_id_label = str(plan.get("resolved_source_id", source_id))
    transfer_ui = theme.TransferProgress(
        resolved_id_label,
        done_description=resolved_id_label,
        stage_labels=_CLONE_STAGE_LABELS,
        file_label=lambda rel: "dataset archive" if rel.endswith(".zip") else Path(rel).name,
    )

    try:
        with transfer_ui:
            result = agent.clone(
                source_id=source_id,
                output_dir=output_dir,
                version=version,
                method=method,
                progress_callback=transfer_ui.on_progress,
                on_files_resolved=transfer_ui.on_files_resolved,
                on_file_done=transfer_ui.on_file_done,
                stage_callback=transfer_ui.on_stage,
                api_url=api_url,
                token=resolved_token,
                service_instance=resolved,
                dev_user_id=resolved_dev_user,
            )
    except typer.Exit:
        raise
    except Exception as exc:
        console.print()
        console.print(theme.error_panel(
            escape(str(exc)),
            title="Clone failed",
            hint="try `mdf clone <id> --transfer` for cross-endpoint / Globus-only datasets",
        ))
        raise typer.Exit(code=1)

    if json_output:
        payload = {"plan": plan, "result": result}
        print(json.dumps(payload, indent=2))
        if not result.get("success"):
            raise typer.Exit(code=1)
        return

    if derive:
        from mdf.models.config import DerivedFrom

        derive_root = Path(output_dir).resolve()
        derive_agent = MDFAgent.init_manifest(
            path=str(derive_root),
            title=f"Derived from {result.get('resolved_source_id') or source_id}",
            authors=["Unknown"],
        )
        derive_agent.manifest.derived_from = [
            DerivedFrom(source_id=result.get("resolved_source_id") or source_id, relationship="derived")
        ]
        derive_agent.save_manifest()
    _render_clone_result(result, derive=derive)


# ---------------------------------------------------------------------------
# Explore commands
# ---------------------------------------------------------------------------

@app.command("search", rich_help_panel=PANEL_EXPLORE)
def search(
    query: str = typer.Argument(..., help="Search query"),
    search_type: str = typer.Option("all", "--type", "-t", help="all, datasets, or streams"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help="Use vector semantic search (title + description embeddings). Datasets only.",
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    brief: bool = typer.Option(False, "--brief", "-b", help="Minimal JSON: title, authors, doi (requires --json)"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance (staging/prod/dev/local)"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id (X-User-Id)"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API base URL"),
):
    """Search public datasets and streams.

    Examples:
        mdf search "perovskite"
        mdf search "XRD" --type streams
        mdf search "iron oxide" --limit 5
        mdf search "perovskite" --json --brief
        mdf search "battery cathode materials" --semantic
    """
    resolved = resolve_service(service)
    client = read_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user)

    with api_spinner("Searching (semantic)..." if semantic else "Searching..."):
        if semantic:
            result = client.search_semantic(query, limit=limit)
            # Semantic search now requires auth (it spends the server's OpenAI key).
            # An anonymous caller gets a 401 {"detail": ...} — surface a login hint.
            detail = str(result.get("detail") or result.get("error") or "")
            if not result.get("results") and any(
                kw in detail.lower() for kw in ("auth", "token", "credential", "401", "unauthorized")
            ):
                if json_output:
                    print(json.dumps(result, indent=2))
                else:
                    console.print()
                    console.print(theme.error_panel(
                        "Semantic search requires login.",
                        title="Login required",
                        border="yellow",
                        hint=f"run `mdf login --service {resolved}`, then retry with --semantic",
                    ))
                client.close()
                return
            if not result.get("available", True):
                reason = result.get("reason") or "Semantic search unavailable"
                if json_output:
                    print(json.dumps(result, indent=2))
                else:
                    console.print()
                    console.print(theme.error_panel(
                        "Semantic search unavailable",
                        title="Semantic search",
                        border="yellow",
                        detail=escape(str(reason)),
                        hint="keyword search still works: drop --semantic",
                    ))
                client.close()
                return
            # Normalize shape so downstream rendering matches keyword search
            result.setdefault("query", query)
            result.setdefault("total", len(result.get("results") or []))
        else:
            result = client.search(query, search_type=search_type, limit=limit)
    client.close()

    if json_output:
        if brief:
            items = []
            for item in result.get("results", []):
                entry = {
                    "title": item.get("title", ""),
                    "authors": item.get("authors", []),
                    "doi": item.get("doi", ""),
                }
                items.append(entry)
            print(json.dumps(items, indent=2))
        else:
            print(json.dumps(result, indent=2))
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
        return

    if result.get("results"):
        total = result.get("total", 0)
        console.print(
            f"\n{theme.SEARCH} [bold]Found[/bold] {theme.number(total)} [bold]results for[/bold] "
            f"[accent]'{escape(str(result.get('query')))}'[/accent]\n"
        )

        table = theme.table()
        table.add_column("#", style="secondary", width=3, justify="right")
        table.add_column("Type", width=8, style="secondary")
        table.add_column("Title", max_width=40)
        table.add_column("ID", max_width=28, no_wrap=True, style="id")
        table.add_column("Status", no_wrap=True)
        table.add_column("DOI", style="secondary", max_width=24)

        for i, item in enumerate(result["results"], 1):
            if item.get("type") == "dataset":
                table.add_row(
                    str(i),
                    "dataset",
                    escape(str(item.get("title", "Untitled"))),
                    f"{escape(str(item.get('source_id')))} v{escape(str(item.get('version')))}",
                    format_status_badge(item.get("status", "")),
                    escape(str(item.get("doi", ""))),
                )
            else:
                table.add_row(
                    str(i),
                    "stream",
                    escape(str(item.get("title", "Untitled"))),
                    escape(str(item.get("stream_id", ""))),
                    f"{theme.number(item.get('file_count', 0))} files",
                    "",
                )

        console.print(table)
        first = result["results"][0]
        if first.get("source_id"):
            console.print(f"\n[hint]Next: mdf show {escape(str(first['source_id']))}[/hint]")
    else:
        hints = [f"broaden the query, e.g. `mdf search \"{escape(query.split()[0] if query.split() else query)}\"`"]
        if not semantic:
            hints.append("try `--semantic` for meaning-based matching")
        hints.append("list your own submissions with `mdf list`")
        console.print()
        console.print(theme.empty_panel(
            f"No results found for '{escape(query)}'",
            title="no matches",
            hints=hints,
        ))


@app.command("list", rich_help_panel=PANEL_EXPLORE)
def list_datasets(
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
    status_filter: Optional[str] = typer.Option(None, "--status", help="Filter by status"),
    search: Optional[str] = typer.Option(None, "--search", help="Filter by title, source ID, or DOI"),
    latest_only: bool = typer.Option(False, "--latest-only", help="Only show latest versions"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """List your submitted datasets.

    Examples:
        mdf list
        mdf list --limit 50
    """
    resolved = resolve_service(service)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    with api_spinner("Loading datasets..."):
        result = client.submissions()
    client.close()

    submissions = result.get("submissions", [])
    filtered_submissions = []
    query = (search or "").strip().lower()
    for sub in submissions:
        mdata = sub.get("dataset_mdata", {})
        if isinstance(mdata, str):
            try:
                mdata = json.loads(mdata)
            except Exception:
                mdata = {}
        status_value = sub.get("status", "")
        if status_filter and status_value != status_filter:
            continue
        if latest_only and isinstance(mdata, dict) and mdata.get("latest") is False:
            continue
        if query:
            haystack = " ".join(
                str(value or "")
                for value in (
                    sub.get("source_id"),
                    sub.get("doi"),
                    sub.get("dataset_doi"),
                    sub.get("title"),
                    mdata.get("title") if isinstance(mdata, dict) else "",
                )
            ).lower()
            if query not in haystack:
                continue
        filtered_submissions.append((sub, mdata))

    filtered_submissions = filtered_submissions[:limit]

    if json_output:
        filtered_result = dict(result)
        filtered_result["submissions"] = [sub for sub, _mdata in filtered_submissions]
        if "total" in filtered_result:
            filtered_result["total"] = len(filtered_submissions)
        if "count" in filtered_result:
            filtered_result["count"] = len(filtered_submissions)
        print(json.dumps(filtered_result, indent=2))
        return

    if not filtered_submissions and not is_success(result):
        # Distinguish "you have no datasets" from "we could not ask".
        info = classify_api_error(result)
        console.print()
        console.print(theme.error_panel(
            escape(str(info["message"])),
            title="Listing failed",
            code=info["code"],
            detail=escape(str(info["detail"])) if info["detail"] else None,
            hint=info["hint"],
            border=info["border"] or "red",
        ))
        return

    if not filtered_submissions:
        filters_used = bool(status_filter or search or latest_only)
        hints = ["publish your first dataset with `mdf publish --submit`"]
        if filters_used:
            hints.insert(0, "loosen the filters (--status / --search / --latest-only)")
        else:
            hints.append("not seeing your data? check `mdf status --auth`")
        console.print()
        console.print(theme.empty_panel(
            "No datasets found.", title="no datasets", hints=hints
        ))
        return

    console.print(
        f"\n[bold]Your datasets[/bold] [secondary]([/secondary]{theme.number(len(filtered_submissions))}[secondary])[/secondary]\n"
    )
    table = theme.table()
    table.add_column("Source ID", max_width=28, no_wrap=True, style="id")
    table.add_column("Title", max_width=40)
    table.add_column("Version", style="secondary", no_wrap=True, justify="right")
    table.add_column("Status", no_wrap=True)
    table.add_column("Updated", style="secondary", no_wrap=True)

    for sub, mdata in filtered_submissions:
        title_str = mdata.get("title") or sub.get("title") or "Untitled"
        status_str = format_status_badge(sub.get("status", ""))
        updated = (sub.get("updated_at") or sub.get("created_at") or "")[:19]
        table.add_row(
            escape(str(sub.get("source_id", ""))),
            escape(str(title_str)),
            escape(str(sub.get("version", ""))),
            status_str,
            escape(str(updated)),
        )
    console.print(table)


@app.command("related", rich_help_panel=PANEL_EXPLORE)
def related(
    source_id: str = typer.Argument(..., help="Source ID (or DOI) of the dataset"),
    by: str = typer.Option("author", "--by", help="Relation type: author | similar"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
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
        console.print(theme.error_panel(
            f"Unsupported relation type: {escape(str(by))}",
            title="Bad option",
            hint="use `--by author` or `--by similar`",
        ))
        raise typer.Exit(code=2)

    resolved = resolve_service(service)
    client = read_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user)
    resolved_id = _resolve_identifier_with_notice(client, source_id)

    if by == "similar":
        spinner_text = "Finding similar datasets..."
    else:
        spinner_text = "Finding related datasets..."

    with api_spinner(spinner_text):
        if by == "similar":
            result = client.similar_by_embedding(resolved_id, limit=limit)
        else:
            result = client.related_by_author(resolved_id, limit=limit)
    client.close()

    if json_output:
        print(json.dumps(result, indent=2))
        return

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
        table.add_column("Source ID", max_width=28, no_wrap=True, style="id")
        table.add_column("Title", max_width=40)
        table.add_column("Score", style="value", justify="right")
        table.add_column("Year", style="secondary", no_wrap=True, justify="right")
        table.add_column("DOI", style="secondary", max_width=24)
        for row in results:
            score = row.get("score")
            score_str = f"{float(score):.3f}" if score is not None else ""
            table.add_row(
                escape(str(row.get("source_id", ""))),
                escape(str(row.get("title") or "Untitled")),
                score_str,
                escape(str(row.get("publication_year") or "")),
                escape(str(row.get("doi") or "")),
            )
        console.print(table)
        return

    console.print(
        f"\n[bold]{theme.number(len(results))} related dataset(s)[/bold] "
        f"sharing authors with {theme.ident(resolved_id)}\n"
    )
    table = theme.table()
    table.add_column("Source ID", max_width=28, no_wrap=True, style="id")
    table.add_column("Title", max_width=40)
    table.add_column("Shared", style="value", justify="right")
    table.add_column("Year", style="secondary", no_wrap=True, justify="right")
    table.add_column("DOI", style="secondary", max_width=24)
    for row in results:
        table.add_row(
            escape(str(row.get("source_id", ""))),
            escape(str(row.get("title") or "Untitled")),
            escape(str(row.get("shared_authors") or "")),
            escape(str(row.get("publication_year") or "")),
            escape(str(row.get("doi") or "")),
        )
    console.print(table)


@app.command(rich_help_panel=PANEL_EXPLORE)
def show(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    cite: bool = typer.Option(False, "--cite", "-c", help="Include citation"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Show dataset details.

    Examples:
        mdf show my_dataset_v1
        mdf show my_dataset_v1 --cite
        mdf show my_dataset_v1 --json
    """
    resolved = resolve_service(service)
    client = read_client(api_url=api_url, token=token, service=resolved, dev_user=dev_user)
    source_id = _resolve_identifier_with_notice(client, source_id)
    with api_spinner("Loading dataset..."):
        result = client.get_card(source_id, version=version)
        citation_result = None
        if cite and is_success(result):
            citation_result = client.get_citation(source_id, format="apa", version=version)
    client.close()

    if json_output:
        output = {"card": result}
        if citation_result:
            output["citation"] = citation_result
        print(json.dumps(output, indent=2))
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
        table.add_row("Authors", escape(", ".join(c["authors"])))
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

    console.print(
        f"\n[hint]Next: mdf dataset open {source_id} | mdf dataset cite {source_id} "
        f"| mdf dataset preview {source_id}[/hint]"
    )
    console.print()


# ---------------------------------------------------------------------------
# Hidden aliases — old top-level commands that now live in sub-apps
# ---------------------------------------------------------------------------

@app.command(hidden=True)
def init(
    path: str = typer.Argument(".", help="Directory to create mdf.yaml in"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Dataset title"),
    author: Optional[List[str]] = typer.Option(None, "--author", "-a", help="Author name (repeatable)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Dataset description"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Dataset publisher"),
    publication_year: Optional[int] = typer.Option(None, "--year", "-y", help="Publication year"),
):
    """Create an mdf.yaml manifest for a dataset directory."""
    from mdf.cli.manifest_cmd import manifest_init
    manifest_init(
        path=path,
        title=title,
        author=author,
        description=description,
        publisher=publisher,
        publication_year=publication_year,
    )


@app.command(hidden=True)
def validate(
    data: Optional[List[str]] = typer.Argument(None, help="Data paths to validate (optional)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance for context"),
):
    """Validate manifest before publishing."""
    manifest_path = Path.cwd() / "mdf.yaml"
    if manifest_path.exists():
        agent = MDFAgent.from_manifest(".")
    elif data:
        from mdf.models.config import ManifestConfig
        agent = MDFAgent(manifest=ManifestConfig(data_sources=list(data)))
        console.print(theme.check_line(
            "active", "No mdf.yaml found — validating data paths only", indent=0
        ))
    else:
        console.print()
        console.print(theme.error_panel(
            "No mdf.yaml found and no data paths provided",
            title="Nothing to validate",
            hint="run `mdf setup` to create a manifest, or pass data paths",
        ))
        raise typer.Exit(code=1)

    root = agent.root or Path.cwd()
    preflight = run_preflight(
        agent.manifest,
        root=root,
        service=resolve_service(service),
        submit=False,
    )
    issues = preflight.get("issues", [])
    errors = [issue["message"] for issue in issues if issue["severity"] == "blocking"]
    warnings = [issue["message"] for issue in issues if issue["severity"] == "warning"]

    if json_output:
        print(json.dumps({"success": not errors, "errors": errors, "warnings": warnings, "preflight": preflight}, indent=2))
        if errors:
            raise typer.Exit(code=1)
        return

    _render_preflight(preflight, title="Validation")

    if errors:
        console.print()
        console.print(theme.error_panel(
            "Validation failed",
            title="Validation",
            detail=f"{len(errors)} blocking issue(s) must be fixed before publishing.",
            hint="fix the ✗ items above, then re-run `mdf validate`",
        ))
        raise typer.Exit(code=1)

    console.print()
    console.print(theme.mark("ok", "Validation passed", style="ok.bold"))


@app.command(hidden=True)
def whoami(
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show current authentication status."""
    from mdf.auth.globus import DEFAULT_TOKEN_PATH, is_logged_in

    resolved = resolve_service(service)
    cached = is_logged_in(service_instance=resolved)
    env_token = bool(os.environ.get("MDF_CONNECT_TOKEN"))
    status_str = "authenticated" if (cached or env_token) else "not authenticated"

    result = {
        "success": True,
        "service": resolved,
        "status": status_str,
        "token_store": str(DEFAULT_TOKEN_PATH),
        "env_token_set": env_token,
    }

    if json_output:
        print(json.dumps(result, indent=2))
        return

    _render_identity(
        service=resolved,
        status_str=status_str,
        token_store=str(DEFAULT_TOKEN_PATH),
        env_token=env_token,
    )


@app.command("watch", hidden=True)
def watch_cmd(
    source_id: Optional[str] = typer.Argument(None, help="Source ID (default: last published)"),
    interval: int = typer.Option(10, "--interval", "-i", help="Poll interval in seconds"),
    timeout: int = typer.Option(1800, "--timeout", help="Timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Watch a dataset until it reaches a terminal state."""
    resolved = resolve_service(service)

    lookup_id = source_id
    if not lookup_id:
        cfg = GlobalConfig()
        lookup_id = cfg.last_source_id
        if not lookup_id:
            console.print(theme.error_panel(
                "No source_id provided and no last published dataset.",
                title="Nothing to watch",
                hint="usage: mdf status --watch <source_id>",
            ))
            raise typer.Exit(code=1)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    lookup_id = _resolve_identifier_with_notice(client, lookup_id)
    client.close()

    raise typer.Exit(
        code=_watch_submission(
            lookup_id,
            interval=interval,
            timeout=timeout,
            service=resolved,
            api_url=api_url,
            token=token,
            dev_user=dev_user,
            json_output=json_output,
            announce=True,
        )
    )


@app.command(hidden=True)
def doctor(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
):
    """Run diagnostics on your MDF Agent setup."""
    from mdf.cli.config_cmd import doctor as config_doctor
    config_doctor(json_output=json_output, service=service, api_url=api_url)


# Hidden aliases for commands moved to dataset sub-app
@app.command(hidden=True, name="cite")
def cite_alias(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    format: str = typer.Option("apa", "--format", "-f", help="Citation format"),
    copy: bool = typer.Option(False, "--copy", help="Copy to clipboard"),
    version: Optional[str] = typer.Option(None, "--version", "-v"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Get citation for a dataset."""
    from mdf.cli.dataset_cmd import cite
    cite(source_id=source_id, format=format, copy=copy, version=version,
         json_output=json_output, service=service, api_url=api_url, token=token, dev_user=dev_user)


@app.command("open", hidden=True)
def open_alias(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    url: bool = typer.Option(False, "--url"),
    version: Optional[str] = typer.Option(None, "--version", "-v"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Open a dataset in the browser."""
    from mdf.cli.dataset_cmd import open_dataset
    open_dataset(source_id=source_id, url=url, version=version,
                 service=service, api_url=api_url, token=token, dev_user=dev_user)


@app.command(hidden=True, name="preview")
def preview_alias(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    sample: bool = typer.Option(False, "--sample"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Preview a dataset's files and data."""
    from mdf.cli.dataset_cmd import preview
    preview(source_id=source_id, sample=sample, json_output=json_output,
            service=service, api_url=api_url, token=token, dev_user=dev_user)


@app.command(hidden=True, name="versions")
def versions_alias(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    limit: int = typer.Option(50, "--limit", "-n"),
    offset: int = typer.Option(0, "--offset"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Show version history for a dataset."""
    from mdf.cli.dataset_cmd import versions
    versions(source_id=source_id, limit=limit, offset=offset, json_output=json_output,
             service=service, api_url=api_url, token=token, dev_user=dev_user)


@app.command(hidden=True, name="diff")
def diff_alias(
    source_id: str = typer.Argument(..., help="Dataset source ID"),
    from_version: str = typer.Option(..., "--from"),
    to_version: str = typer.Option(..., "--to"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Show metadata diff between two versions."""
    from mdf.cli.dataset_cmd import diff
    diff(source_id=source_id, from_version=from_version, to_version=to_version,
         json_output=json_output, service=service, api_url=api_url, token=token, dev_user=dev_user)


@app.command(hidden=True, name="edit")
def edit_alias(
    source_id: str = typer.Argument(..., help="Source ID"),
    title: Optional[str] = typer.Option(None, "--title", "-t"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    keywords: Optional[List[str]] = typer.Option(None, "--keyword", "-k"),
    version: Optional[str] = typer.Option(None, "--version", "-v"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Edit metadata on a submission."""
    from mdf.cli.dataset_cmd import edit
    edit(source_id=source_id, title=title, description=description, keywords=keywords,
         version=version, json_output=json_output, service=service, api_url=api_url,
         token=token, dev_user=dev_user)


@app.command(hidden=True, name="withdraw")
def withdraw_alias(
    source_id: str = typer.Argument(..., help="Source ID"),
    reason: Optional[str] = typer.Option(None, "--reason", "-r"),
    version: Optional[str] = typer.Option(None, "--version", "-v"),
    yes: bool = typer.Option(False, "--yes"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Withdraw a pending_curation submission."""
    from mdf.cli.dataset_cmd import withdraw
    withdraw(source_id=source_id, reason=reason, version=version, yes=yes,
             json_output=json_output, service=service, api_url=api_url, token=token, dev_user=dev_user)


@app.command(hidden=True, name="resubmit")
def resubmit_alias(
    source_id: str = typer.Argument(..., help="Source ID"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n"),
    version: Optional[str] = typer.Option(None, "--version", "-v"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Resubmit a rejected dataset."""
    from mdf.cli.dataset_cmd import resubmit
    resubmit(source_id=source_id, notes=notes, version=version,
             json_output=json_output, service=service, api_url=api_url, token=token, dev_user=dev_user)


# Hidden aliases for commands moved to admin sub-app
@app.command(hidden=True, name="pending")
def pending_alias(
    limit: int = typer.Option(20, "--limit", "-n"),
    organization: Optional[str] = typer.Option(None, "--organization", "-o"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """List datasets awaiting curation."""
    from mdf.cli.admin_cmd import pending
    pending(limit=limit, organization=organization, json_output=json_output,
            service=service, api_url=api_url, token=token, dev_user=dev_user)


@app.command(hidden=True, name="approve")
def approve_alias(
    source_id: str = typer.Argument(..., help="Source ID"),
    mint_doi: bool = typer.Option(True, "--mint-doi/--no-mint-doi"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n"),
    version: Optional[str] = typer.Option(None, "--version", "-v"),
    yes: bool = typer.Option(False, "--yes"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Approve a dataset for publication."""
    from mdf.cli.admin_cmd import approve
    approve(source_id=source_id, mint_doi=mint_doi, notes=notes, version=version,
            yes=yes, json_output=json_output, service=service, api_url=api_url,
            token=token, dev_user=dev_user)


@app.command(hidden=True, name="reject")
def reject_alias(
    source_id: str = typer.Argument(..., help="Source ID"),
    reason: str = typer.Option(..., "--reason", "-r"),
    suggestions: Optional[str] = typer.Option(None, "--suggestions"),
    version: Optional[str] = typer.Option(None, "--version", "-v"),
    yes: bool = typer.Option(False, "--yes"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Reject a dataset and return to submitter."""
    from mdf.cli.admin_cmd import reject
    reject(source_id=source_id, reason=reason, suggestions=suggestions, version=version,
           yes=yes, json_output=json_output, service=service, api_url=api_url,
           token=token, dev_user=dev_user)


@app.command("delete", hidden=True)
def delete_alias(
    source_id: str = typer.Argument(..., help="Source ID"),
    reason: str = typer.Option(..., "--reason", "-r"),
    version: Optional[str] = typer.Option(None, "--version", "-v"),
    yes: bool = typer.Option(False, "--yes"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Soft-delete a submission."""
    from mdf.cli.admin_cmd import delete_dataset
    delete_dataset(source_id=source_id, reason=reason, version=version,
                   yes=yes, json_output=json_output, service=service, api_url=api_url,
                   token=token, dev_user=dev_user)


@app.command(hidden=True, name="stats")
def stats_alias(
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Show admin statistics."""
    from mdf.cli.admin_cmd import stats
    stats(json_output=json_output, service=service, api_url=api_url, token=token, dev_user=dev_user)


# Hidden alias for update (publish --update covers this)
@app.command(hidden=True, name="update")
def update_alias(
    source_id: Optional[str] = typer.Argument(None, help="Source ID of dataset to update"),
    data: Optional[List[str]] = typer.Option(None, "--data", "-D", help="Data paths/URIs"),
    title: Optional[str] = typer.Option(None, "--title", "-t"),
    author: Optional[List[str]] = typer.Option(None, "--author", "-a"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    dry_run: bool = typer.Option(True, "--dry-run/--submit"),
    json_output: bool = typer.Option(False, "--json"),
    preflight_only: bool = typer.Option(False, "--preflight-only"),
    no_watch: bool = typer.Option(False, "--no-watch"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Update an existing published dataset."""
    from mdf.models.config import ManifestConfig

    resolved = resolve_service(service)

    resolved_source_id = source_id
    if not resolved_source_id:
        cfg = GlobalConfig()
        resolved_source_id = cfg.last_source_id
        if not resolved_source_id:
            console.print(theme.error_panel(
                "No source_id provided and no last published dataset.",
                title="Nothing to update",
                hint="usage: mdf publish --update --data ./new_data/ --submit",
            ))
            raise typer.Exit(code=1)
        if not json_output:
            console.print(
                f"[secondary]Using last published dataset:[/secondary] {theme.ident(resolved_source_id)}"
            )

    cfg = GlobalConfig()
    existing_title = resolved_source_id
    existing_authors: List[str] = []
    try:
        _base = api_url or _api_url_for_service(resolved)
        _client = BackendClient.authenticated(
            base_url=_base, token=token, service_instance=resolved, dev_user_id=dev_user,
        )
        try:
            resolved_source_id = _resolve_identifier_with_notice(_client, resolved_source_id)
            with api_spinner("Fetching existing metadata..."):
                card = _client.get_card(resolved_source_id)
            if is_success(card) and card.get("card"):
                c = card["card"]
                existing_title = c.get("title", resolved_source_id)
                existing_authors = c.get("authors", [])
        finally:
            _client.close()
    except Exception:
        pass

    data_sources = list(data) if data else []
    manifest = ManifestConfig(
        title=title or existing_title,
        authors=author or existing_authors or [resolved_source_id],
        description=description,
        data_sources=data_sources,
        publisher=cfg.publisher,
        organization=cfg.organization,
    )
    manifest.custom = {"mdf_source_id": resolved_source_id}

    preflight = run_preflight(
        manifest,
        root=Path.cwd(),
        service=resolved,
        api_url=api_url or _api_url_for_service(resolved),
        token=token,
        dev_user=dev_user,
        submit=preflight_only or not dry_run,
    )

    if preflight_only:
        if json_output:
            print(json.dumps(preflight, indent=2))
        else:
            _render_preflight(preflight, title="Update preflight")
        if not preflight.get("success"):
            raise typer.Exit(code=1)
        return

    if not preflight.get("success"):
        if json_output:
            print(json.dumps({"success": False, "preflight": preflight}, indent=2))
        else:
            _render_preflight(preflight, title="Update preflight")
            console.print()
            console.print(theme.error_panel(
                "Update blocked by preflight issues",
                title="Blocked",
                hint="fix the ✗ items above, then re-run with --preflight-only",
            ))
        raise typer.Exit(code=1)

    agent = MDFAgent(root=None, manifest=manifest)
    payload = agent.build_submission(test=False, update=True)

    if dry_run:
        if json_output:
            print(json.dumps({"success": True, "dry_run": True, "payload": payload, "preflight": preflight}, indent=2))
            return
        _render_preflight(preflight, title="Update preflight")
        target = api_url or _api_url_for_service(resolved)
        console.print()
        console.print(theme.rule("Dry run — would update"))
        console.print(f"[label]Target:[/label] {escape(str(target))} [secondary]({escape(resolved)})[/secondary]")
        console.print(f"[label]Source ID:[/label] {theme.ident(resolved_source_id)}")
        console.print()
        console.print(theme.json_syntax(payload))
        return

    transfer = theme.TransferProgress("Uploading", done_description="Upload complete")

    try:
        with transfer:
            result = agent.publish(
                test=False,
                update=True,
                dry_run=False,
                token=token,
                service_instance=resolved,
                api_url=api_url,
                dev_user_id=dev_user,
                progress_callback=transfer.on_progress,
                on_files_resolved=transfer.on_files_resolved,
                on_file_done=transfer.on_file_done,
            )
    except RuntimeError as exc:
        console.print()
        console.print(theme.error_panel(
            escape(str(exc)),
            title="Upload failed",
            hint="check connectivity with `mdf config doctor`, then retry the update",
        ))
        raise typer.Exit(code=1)

    if json_output:
        print(json.dumps(result, indent=2))
        if not result.get("success"):
            raise typer.Exit(code=1)
        return

    if result.get("success"):
        new_version = result.get("version")
        detail = theme.detail_table(label_width=11)
        detail.add_row("Source ID", theme.ident(result.get("source_id")))
        detail.add_row("Service", escape(str(resolved)))
        detail.add_row("Status", format_status_badge("pending_curation"))
        if new_version:
            detail.add_row("Version", theme.ident(new_version))
        console.print()
        console.print(theme.success_panel(
            detail,
            title="Updated successfully!",
            emoji=theme.PACKAGE,
            subtitle="awaiting curation",
        ))

        if result.get("source_id"):
            cfg = GlobalConfig()
            cfg.record_publish(result.get("source_id"), new_version, resolved)
            console.print(f"[hint]Try: mdf status {result.get('source_id')} | mdf show {result.get('source_id')}[/hint]")
            if no_watch:
                console.print(f"[hint]Watch: mdf status --watch {result.get('source_id')}[/hint]")
            else:
                raise typer.Exit(
                    code=_watch_submission(
                        result.get("source_id"),
                        interval=5,
                        timeout=1800,
                        service=resolved,
                        api_url=api_url,
                        token=token,
                        dev_user=dev_user,
                        announce=True,
                        stop_on_statuses={"pending_curation"},
                    )
                )
    else:
        require_success(result, error_prefix="Update failed")


# ---------------------------------------------------------------------------
# Import (cross-publish from external repos)
# ---------------------------------------------------------------------------

@app.command("import", rich_help_panel=PANEL_PUBLISH)
def import_external(
    identifier: str = typer.Argument(..., help="External identifier (e.g. zenodo:12345, https://zenodo.org/records/12345)"),
    output_dir: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory for downloaded files"),
    dry_run: bool = typer.Option(True, "--dry-run/--submit", help="Preview metadata without downloading"),
    test: bool = typer.Option(False, "--test", help="Submit to test environment"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    no_watch: bool = typer.Option(False, "--no-watch", help="Do not watch after submit"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance (staging/prod/dev/local)"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
):
    """Import a dataset from an external repository (Zenodo, etc.).

    Fetches metadata and data files, then publishes to MDF with
    provenance linking back to the original source.

    Examples:
        mdf import zenodo:12345                    # preview metadata
        mdf import zenodo:12345 --submit           # download + submit
        mdf import zenodo:12345 -o ./data --submit # custom output dir
    """
    from mdf.importers.registry import resolve_adapter

    adapter = resolve_adapter(identifier)
    if not adapter:
        console.print()
        console.print(theme.error_panel(
            f"No adapter found for: {escape(str(identifier))}",
            title="Unsupported source",
            detail="Supported formats: zenodo:ID, https://zenodo.org/records/ID",
            hint="check the identifier, or open an issue to request a new repository adapter",
        ))
        raise typer.Exit(code=1)

    # Fetch metadata
    with api_spinner(f"Fetching metadata from {adapter.name()}…"):
        try:
            metadata = adapter.fetch_metadata(identifier)
        except Exception as exc:
            console.print()
            console.print(theme.error_panel(
                escape(str(exc)),
                title="Failed to fetch metadata",
                hint=f"confirm the record exists and is public on {adapter.name()}",
            ))
            raise typer.Exit(code=1)

    # Show metadata panel
    if not json_output:
        table = theme.detail_table(label_width=12)
        table.add_row("Source", theme.ident(adapter.name()))
        table.add_row("Title", escape(str(metadata.get("title", ""))))
        authors_str = ", ".join(a.get("name", "") for a in metadata.get("authors", []))
        table.add_row("Authors", escape(authors_str))
        desc = metadata.get("description", "")
        if len(desc) > 200:
            desc = desc[:200] + "..."
        table.add_row("Description", escape(str(desc)))
        if metadata.get("keywords"):
            table.add_row("Keywords", escape(", ".join(metadata["keywords"])))
        if metadata.get("license"):
            lic = metadata["license"]
            table.add_row("License", escape(lic.get("name", "") if isinstance(lic, dict) else str(lic)))
        if metadata.get("doi"):
            table.add_row("DOI", f"[link=https://doi.org/{metadata['doi']}]{escape(str(metadata['doi']))}[/link]")
        table.add_row("URL", escape(str(metadata.get("url", ""))))
        files = metadata.get("file_urls", [])
        total_size = sum(f.get("size", 0) for f in files)
        table.add_row(
            "Files",
            f"{theme.number(len(files))} files [secondary]({_human_size(total_size)})[/secondary]",
        )

        console.print()
        console.print(theme.panel(
            table,
            title=f"{theme.PACKAGE} {adapter.name()} import",
            subtitle=escape(str(identifier)),
        ))

    if dry_run:
        if json_output:
            print(json.dumps({"success": True, "dry_run": True, "metadata": metadata}, indent=2))
        else:
            if metadata.get("file_urls"):
                console.print()
                file_table = theme.table()
                file_table.add_column("File")
                file_table.add_column("Size", justify="right", style="value")
                for f in metadata["file_urls"][:10]:
                    file_table.add_row(
                        escape(str(f.get("filename", "?"))),
                        _human_size(f["size"]) if f.get("size") else "-",
                    )
                console.print(file_table)
                if len(metadata["file_urls"]) > 10:
                    console.print(f"[secondary]... and {len(metadata['file_urls']) - 10} more[/secondary]")
            console.print()
            console.print(theme.panel(
                f"[id]mdf import {escape(str(identifier))} --submit[/id]",
                title=f"{theme.PACKAGE} Ready to import",
            ))
        return

    # Download + submit
    resolved = resolve_service(service)
    agent = MDFAgent()

    download = theme.TransferProgress(
        f"Downloading from {adapter.name()}",
        done_description="Download complete",
    )

    try:
        with download:
            result = agent.import_external(
                identifier=identifier,
                output_dir=output_dir,
                metadata=metadata,
                progress_callback=download.on_progress,
                on_files_resolved=download.on_files_resolved,
                on_file_done=download.on_file_done,
            )
    except Exception as exc:
        console.print()
        console.print(theme.error_panel(
            escape(str(exc)),
            title="Import failed",
            hint=f"retry, or download from {adapter.name()} manually and use `mdf publish`",
        ))
        raise typer.Exit(code=1)

    if not result.get("success"):
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            console.print()
            console.print(theme.error_panel(
                escape(str(result.get("error", "Unknown error"))),
                title="Import failed",
                hint=f"retry, or download from {adapter.name()} manually and use `mdf publish`",
            ))
        raise typer.Exit(code=1)

    # Now publish using the generated manifest (includes upload to MDF storage)
    manifest = result["manifest"]
    agent_pub = MDFAgent(root=Path(result["output_dir"]), manifest=manifest)

    if not json_output:
        console.print()
        console.print(theme.mark(
            "ok",
            f"Downloaded {result.get('files_downloaded', 0)} files to {escape(str(result['output_dir']))}",
            style="ok",
        ))
        console.print("[secondary]Uploading to MDF and submitting…[/secondary]")

    upload = theme.TransferProgress("Uploading to MDF", done_description="Upload complete")

    try:
        with upload:
            pub_result = agent_pub.publish(
                test=test,
                dry_run=False,
                token=token,
                service_instance=resolved,
                api_url=api_url,
                dev_user_id=dev_user,
                progress_callback=upload.on_progress,
                on_files_resolved=upload.on_files_resolved,
                on_file_done=upload.on_file_done,
            )
    except RuntimeError as exc:
        console.print()
        console.print(theme.error_panel(
            escape(str(exc)),
            title="Upload failed",
            hint="the download is kept — re-run `mdf publish --submit` from the output directory",
        ))
        raise typer.Exit(code=1)

    if json_output:
        print(json.dumps({"import": result, "publish": pub_result}, indent=2))
        if not pub_result.get("success"):
            raise typer.Exit(code=1)
        return

    if pub_result.get("success"):
        source_id_val = pub_result.get("source_id")
        detail = theme.detail_table(label_width=13)
        detail.add_row("Source ID", theme.ident(source_id_val))
        detail.add_row("External DOI", escape(str(metadata.get("doi", "N/A"))))
        detail.add_row("Service", escape(str(resolved)))
        detail.add_row("Status", format_status_badge("pending_curation"))
        console.print()
        console.print(theme.success_panel(
            detail,
            title="Published successfully!",
            emoji=theme.PACKAGE,
            subtitle="awaiting curation",
        ))
        if source_id_val:
            cfg = GlobalConfig()
            cfg.record_publish(source_id_val, pub_result.get("version"), resolved)
            if no_watch:
                console.print(f"[hint]Watch: mdf status --watch {source_id_val}[/hint]")
            else:
                raise typer.Exit(
                    code=_watch_submission(
                        source_id_val,
                        interval=5,
                        timeout=1800,
                        service=resolved,
                        api_url=api_url,
                        token=token,
                        dev_user=dev_user,
                        announce=True,
                        stop_on_statuses={"pending_curation"},
                    )
                )
    else:
        require_success(pub_result, error_prefix="Publish failed")


# Hidden alias: manifest top-level (now under config)
from mdf.cli.manifest_cmd import app as manifest_app
app.add_typer(manifest_app, name="manifest", hidden=True)


#: Byte formatting lives with the rest of the visual language.
_human_size = theme.human_size


if __name__ == "__main__":
    app()
