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
from typer.core import TyperGroup
from rich import box as rich_box
from rich.markup import escape
from rich.panel import Panel as RichPanel

from mdf.version import __version__
from mdf.auth.globus import interactive_login
from mdf.core.agent import MDFAgent
from mdf.core.exceptions import AuthRequired
from mdf.core.backend_client import BackendClient, _api_url_for_service
from mdf.core.config import GlobalConfig, resolve_service
from mdf.cli import theme
from mdf.cli.formatting import (
    EXIT_AUTH,
    EXIT_CODE_BY_KIND,
    EXIT_ERROR,
    EXIT_NETWORK,
    EXIT_USAGE,
    api_spinner,
    chrome,
    classify_api_error,
    console,
    emit_json,
    emit_json_result,
    err_console,
    exit_code_for,
    fail_auth_required,
    format_authors,
    format_status_badge,
    interactive_stdin,
    is_success,
    json_fail,
    json_ok,
    login_allowed,
    read_client,
    require_success,
    resolve_identifier_with_notice,
    status_code_of,
)
from mdf.cli.backend import app as backend_app
from mdf.cli.stream import app as stream_app
from mdf.cli.config_cmd import app as config_app
from mdf.cli.dataset_cmd import app as dataset_app
from mdf.cli.admin_cmd import app as admin_app
from mdf.cli.skill_cmd import app as skill_app
from mdf.cli.mcp_cmd import app as mcp_app
from mdf.cli.preflight import (
    auth_ready,
    run_preflight,
    suggest_data_sources,
)


class MDFGroup(TyperGroup):
    """Root command group: applies the CLI's login policy to every command.

    Commands may only start an interactive Globus login when
    :func:`~mdf.cli.formatting.login_allowed` says so (TTY on stdin and
    stdout, no ``--json``). Otherwise core raises ``AuthRequired`` — also from
    logins started deep inside ``MDFAgent`` — and it is reported here as the
    ``auth`` error: the JSON envelope under ``--json``, exit code 4.
    """

    def invoke(self, ctx: typer.Context) -> Any:
        json_mode = "--json" in [*ctx._protected_args, *ctx.args]
        with interactive_login(login_allowed(json_mode)):
            try:
                return super().invoke(ctx)
            except AuthRequired as exc:
                fail_auth_required(exc, json_mode=json_mode)


app = typer.Typer(
    cls=MDFGroup,
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
app.add_typer(mcp_app, name="mcp", help="Model Context Protocol server for AI assistants")

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


def _resolve_identifier_with_notice(
    client: BackendClient, raw_id: str, *, json_mode: bool = False
) -> str:
    """Thin alias for the shared resolver — see ``formatting.resolve_identifier_with_notice``."""
    return resolve_identifier_with_notice(client, raw_id, json_mode=json_mode)


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
        except AuthRequired:
            raise
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


def _fetch_identity(
    *,
    service: str,
    api_url: Optional[str] = None,
    token: Optional[str] = None,
    dev_user: Optional[str] = None,
) -> Dict[str, str]:
    """Best-effort ``{"name", "email"}`` for the current credentials.

    Asks the backend (``GET /auth/check``), which reads the identity from the
    token. Never prompts and never fails the caller: any problem (no login,
    network, an old backend) just means no identity is shown.
    """
    try:
        client = BackendClient.authenticated(
            base_url=api_url,
            token=token,
            service_instance=service,
            dev_user_id=dev_user,
            interactive=False,
        )
    except Exception:
        return {}
    try:
        result = client.auth_check()
    except Exception:
        return {}
    finally:
        client.close()
    if not is_success(result):
        return {}
    return {key: str(result[key]) for key in ("name", "email") if result.get(key)}


def _identity_label(identity: Dict[str, str]) -> Optional[str]:
    """``Jane Doe <jane@example.org>`` / ``Jane Doe`` / ``jane@example.org``."""
    name, email = identity.get("name"), identity.get("email")
    if name and email:
        return f"{name} <{email}>"
    return name or email or None


def _render_identity(
    *,
    service: str,
    status_str: str,
    token_store: str,
    method: Optional[str] = None,
    env_token: bool = False,
    env_client: bool = False,
    identity: Optional[Dict[str, str]] = None,
) -> None:
    """The identity panel used by `mdf status --auth` and `mdf whoami`."""
    authenticated = status_str == "authenticated"
    detail = theme.detail_table(label_width=12)
    who = _identity_label(identity or {})
    if who:
        detail.add_row("Signed in as", escape(who))
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
    file_count = plan.get("file_count", plan.get("files_count"))
    if file_count is not None:
        detail.add_row("Files", theme.number(file_count))
    total_size = plan.get("total_bytes") or plan.get("archive_size")
    if total_size is not None:
        detail.add_row("Total size", theme.number(_human_size(total_size)))
    if plan.get("version"):
        detail.add_row("Version", escape(str(plan["version"])))
    auth_needed = plan.get("auth_needed", plan.get("auth_required"))
    if auth_needed is None:
        auth_needed = plan.get("selected_method") == "transfer"
    detail.add_row("Auth needed?", "yes" if auth_needed else "no")
    if plan.get("requested_identifier") != plan.get("resolved_source_id"):
        detail.add_row("Requested", escape(str(plan.get("requested_identifier"))))
    console.print()
    console.print(theme.panel(detail, title="Clone plan"))


def _clone_transfer_task_rows(detail: Any, result: Dict[str, Any]) -> None:
    """Add the Globus Transfer task rows — every task id, not just the first."""
    task_ids = result.get("task_ids") or [
        t for t in [result.get("task_id")] if t
    ]
    if not task_ids:
        return
    label = "Transfer task" if len(task_ids) == 1 else "Transfer tasks"
    detail.add_row(label, "\n".join(theme.ident(t) for t in task_ids))
    monitor = result.get("monitor_url")
    if monitor:
        detail.add_row("Monitor", escape(str(monitor)))


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
            _clone_transfer_task_rows(detail, result)
        # A "successful" result can still be partial (e.g. an extra data source
        # that could not be queued) — never swallow that.
        if result.get("partial"):
            detail.add_row("Completeness", theme.mark("active", "partial — see errors below", style="warn"))
        for err in result.get("errors") or []:
            detail.add_row("Error", theme.mark("err", escape(str(err))))
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
        is_transfer = result.get("method") == "transfer"
        task_ids = result.get("task_ids") or [t for t in [result.get("task_id")] if t]
        if is_transfer:
            lines = [f"Queued {theme.number(len(task_ids))} Globus Transfer task(s)."]
        else:
            lines = [f"Downloaded {theme.number(downloaded)} file(s)."]
        if failed is not None:
            lines.append(f"Failed {theme.number(failed)} file(s).")
        lines.append(f"[label]Path:[/label] {escape(str(result.get('path')))}")
        for task_id in task_ids:
            lines.append(f"[label]Transfer task:[/label] {theme.ident(task_id)}")
        if result.get("monitor_url"):
            lines.append(f"[label]Monitor:[/label] {escape(str(result['monitor_url']))}")
        lines.append("")
        lines.append("[bold]Failures[/bold]" if is_transfer else "[bold]Failed files[/bold]")
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
                title=(
                    f"{theme.ACTIVE} Transfer partially queued"
                    if is_transfer
                    else f"{theme.ACTIVE} Clone partially completed"
                ),
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
    # The service is deliberately not asked for or persisted: a stored
    # `defaults.service` would silently pin this machine to today's default
    # (staging) after the release default flips to prod. Use --service,
    # MDF_SERVICE or `mdf config set defaults.service` to choose explicitly.
    service = resolve_service(None)

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
    save: bool = typer.Option(
        False,
        "--save",
        help="With --token: keep the token for later commands (owner-only file under ~/.config/mdf)",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Never auto-open a browser; print the URL and wait for the pasted code",
    ),
):
    """Authenticate with Globus.

    The login URL is always printed first, then a browser is opened only if this
    looks like a desktop session. Use --no-browser (or set MDF_NO_BROWSER=1) on
    headless hosts where a text browser would take over the terminal.

    For scripts and CI, you can skip interactive login entirely:

      MDF_CLIENT_ID + MDF_CLIENT_SECRET   Globus confidential client credentials
      MDF_CONNECT_TOKEN                   Pre-existing access token
      mdf login --token T --save          Validate T once and keep it for later commands
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

    if save and not token:
        console.print()
        console.print(theme.error_panel(
            "--save needs --token",
            title="Nothing to save",
            hint="mdf login --token <ACCESS_TOKEN> --save  (or plain `mdf login` for a Globus login)",
        ))
        raise typer.Exit(code=EXIT_USAGE)

    identity: Dict[str, str] = {}
    saved_path = None
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
        identity = {key: str(auth_result[key]) for key in ("name", "email") if auth_result.get(key)}
        if save:
            from mdf.core.config import save_token

            saved_path = save_token(token)
    else:
        scope, _rs = get_scopes_for_service(resolved)
        try:
            # Logging in is this command's whole job, so it may prompt whenever
            # a human can type the code — prompts go to stderr, so a piped
            # stdout is fine. With no TTY on stdin it cannot, and says so.
            get_authorizer_for_scopes(
                [scope, DATA_MDF_SCOPE, TRANSFER_SCOPE, GROUPS_SCOPE],
                no_browser=no_browser,
                interactive=interactive_stdin(),
            )
        except AuthRequired:
            console.print()
            console.print(theme.error_panel(
                "Interactive login needs a terminal (stdin is not a TTY).",
                title="Login required",
                hint=(
                    "run `mdf login` in a terminal; for scripts/CI set MDF_CONNECT_TOKEN, "
                    "or MDF_CLIENT_ID + MDF_CLIENT_SECRET, or use `mdf login --token T --save`"
                ),
            ))
            raise typer.Exit(code=EXIT_AUTH)
        identity = _fetch_identity(service=resolved)

    detail = theme.detail_table(label_width=12)
    who = _identity_label(identity)
    if who:
        detail.add_row("Signed in as", escape(who))
    detail.add_row("Service", theme.ident(resolved))
    if token and saved_path:
        detail.add_row("Token", f"saved to {escape(str(saved_path))}")
    elif token:
        detail.add_row("Token", "valid (not saved: pass --save to keep it)")
    else:
        detail.add_row("Token store", escape(str(DEFAULT_TOKEN_PATH)))
    console.print()
    console.print(theme.success_panel(detail, title="Authentication ready"))


@app.command(rich_help_panel=PANEL_AUTH)
def logout():
    """Clear cached credentials."""
    from mdf.auth.globus import DEFAULT_TOKEN_PATH, logout as clear_cached_tokens
    from mdf.core.config import clear_saved_token, saved_token_path

    removed_paths = []
    if clear_cached_tokens():
        removed_paths.append(DEFAULT_TOKEN_PATH)
    if clear_saved_token():
        removed_paths.append(saved_token_path())
    if removed_paths:
        console.print(theme.mark("ok", "Logged out", style="ok"))
        removed = ", ".join(escape(str(path)) for path in removed_paths)
        console.print(f"[hint]removed {removed}[/hint]")
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
        from mdf.core.config import load_saved_token

        cached = is_logged_in(service_instance=resolved)
        env_token = bool(os.environ.get("MDF_CONNECT_TOKEN"))
        env_client = bool(
            os.environ.get("MDF_CLIENT_ID") and os.environ.get("MDF_CLIENT_SECRET")
        )

        if env_client:
            method = "confidential client (MDF_CLIENT_ID)"
        elif env_token:
            method = "environment token (MDF_CONNECT_TOKEN)"
        elif load_saved_token():
            method = "saved token (mdf login --token --save)"
        elif cached:
            method = "cached Globus login"
        else:
            method = None

        authenticated = bool(method)
        status_str = "authenticated" if authenticated else "not authenticated"
        identity = (
            _fetch_identity(service=resolved, api_url=api_url, token=token, dev_user=dev_user)
            if authenticated
            else {}
        )

        # `success` is the answer to "am I authenticated?", not "did this
        # command run" — an unauthenticated caller must be able to branch on it
        # (and on the exit code) without parsing the status string.
        result = {
            "success": authenticated,
            "service": resolved,
            "status": status_str,
            "method": method,
            "name": identity.get("name"),
            "email": identity.get("email"),
            "token_store": str(DEFAULT_TOKEN_PATH),
            "env_token_set": env_token,
            "env_client_set": env_client,
        }

        if json_output:
            if not authenticated:
                json_fail(
                    kind="auth",
                    message="Not authenticated",
                    extra={k: v for k, v in result.items() if k != "success"},
                )
            emit_json(result)
            return

        _render_identity(
            service=resolved,
            status_str=status_str,
            token_store=str(DEFAULT_TOKEN_PATH),
            method=method,
            env_token=env_token,
            env_client=env_client,
            identity=identity,
        )
        if not authenticated:
            raise typer.Exit(code=EXIT_AUTH)
        return

    lookup_id = source_id
    if not lookup_id:
        cfg = GlobalConfig()
        lookup_id = cfg.last_source_id
        if not lookup_id:
            if json_output:
                json_fail(
                    kind="validation",
                    message="No source_id provided and no last published dataset.",
                    hint="usage: mdf status <source_id> — or list yours with `mdf list`",
                )
            console.print()
            console.print(theme.empty_panel(
                "No source_id provided and no last published dataset.",
                title="nothing to check",
                hints=[
                    "usage: mdf status <source_id>",
                    "list your submissions with `mdf list`",
                ],
            ))
            raise typer.Exit(code=EXIT_USAGE)

    # --watch mode: poll until terminal state (absorbs old watch)
    if watch:
        client = BackendClient.authenticated(
            base_url=api_url,
            token=token,
            service_instance=resolved,
            dev_user_id=dev_user,
        )
        lookup_id = _resolve_identifier_with_notice(client, lookup_id, json_mode=json_output)
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
    lookup_id = _resolve_identifier_with_notice(client, lookup_id, json_mode=json_output)
    with api_spinner("Checking status..."):
        result = client.status(lookup_id, version=version)
    client.close()

    if json_output:
        emit_json_result(result)
        return

    if not is_success(result):
        # A 404/401/500 is not a status: report it with the mapped exit code
        # instead of rendering an empty panel and exiting 0.
        require_success(result, error_prefix="Status failed")

    _render_status_details(result, lookup_id)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

#: C10a: staging is the public beta service. Printed on every submit to it.
_STAGING_DOI_NOTICE = (
    "Beta: the staging service mints DataCite test DOIs (prefix 10.23677); "
    "they do not resolve on doi.org."
)


def _targets_staging(resolved: str, api_url: Optional[str]) -> bool:
    """True when a submit goes to the staging service (not an overridden URL)."""
    return (
        resolved.strip().lower() == "staging"
        and not api_url
        and not os.environ.get("MDF_API_URL")
    )


def _fail_usage(
    json_output: bool,
    message: str,
    *,
    title: str,
    hint: str,
    detail: Optional[str] = None,
) -> None:
    """A usage/validation failure in either output mode; exits 2. Never returns."""
    if json_output:
        json_fail(kind="validation", message=message, hint=hint)
    console.print()
    console.print(theme.error_panel(escape(message), title=title, detail=detail, hint=hint))
    raise typer.Exit(code=EXIT_USAGE)


def _looks_like_dataset_id(value: str) -> bool:
    """Whether the first positional after ``--update`` names a dataset, not data.

    Anything that exists on disk or carries a URI scheme is data; the rest is
    an ID (or DOI) of the dataset to update.
    """
    return "://" not in value and not Path(value).exists()


@app.command(rich_help_panel=PANEL_PUBLISH)
def publish(
    data: Optional[List[str]] = typer.Argument(
        None, help="Data paths/URIs to publish (with --update, an optional dataset ID first)"
    ),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Dataset title"),
    author: Optional[List[str]] = typer.Option(None, "--author", "-a", help="Author name (repeatable)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Dataset description"),
    dry_run: bool = typer.Option(True, "--dry-run/--submit", help="Preview without submitting"),
    test: bool = typer.Option(False, "--test", help="Submit to test environment"),
    update: bool = typer.Option(
        False,
        "--update",
        "-u",
        help="Publish a new version of an existing dataset: --update [ID] "
        "(default ID: source_id in mdf.yaml, then your last publish)",
    ),
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

    The first successful publish writes the new dataset's source_id into
    mdf.yaml. To publish a new version of it later:

        mdf publish --update --submit                  # ID from mdf.yaml / last publish
        mdf publish --update my_dataset_id --submit    # explicit ID (or DOI)
        mdf publish --update my_dataset_id ./new_data/ --submit

    An update starts from the dataset's current metadata (description,
    keywords, license, funding, related works, ...) and applies your changes
    (mdf.yaml fields and --title/--author/--description) on top. Without data
    paths it is a metadata-only update that keeps the previous files.

    By default, performs a dry run showing the payload.
    Use --submit to actually send to MDF Connect.
    """
    data_args = list(data or [])
    update_id: Optional[str] = None
    if update and data_args and _looks_like_dataset_id(data_args[0]):
        update_id, data_args = data_args[0], data_args[1:]
    _run_publish(
        data=data_args,
        title=title,
        author=author,
        description=description,
        dry_run=dry_run,
        test=test,
        update=update,
        update_id=update_id,
        json_output=json_output,
        preflight_only=preflight_only,
        no_watch=no_watch,
        service=service,
        api_url=api_url,
        token=token,
        dev_user=dev_user,
    )


def _run_publish(
    *,
    data: List[str],
    title: Optional[str],
    author: Optional[List[str]],
    description: Optional[str],
    dry_run: bool,
    test: bool,
    update: bool,
    update_id: Optional[str],
    json_output: bool,
    preflight_only: bool,
    no_watch: bool,
    service: Optional[str],
    api_url: Optional[str],
    token: Optional[str],
    dev_user: Optional[str],
) -> None:
    """``mdf publish`` (and the hidden ``mdf update`` alias)."""
    from mdf.core.manifest import manifest_from_metadata, merge_manifests, write_source_id
    from mdf.models.config import ManifestConfig

    resolved = resolve_service(service)

    manifest_path = Path.cwd() / "mdf.yaml"
    manifest_mode = False

    if data:
        if not update:
            for flag, value in (("--title", title), ("--author", author)):
                if not value:
                    _fail_usage(
                        json_output,
                        f"Direct mode requires {flag}",
                        title="Missing metadata",
                        hint='example: mdf publish ./data/ --title "My Dataset" --author "Jane"',
                    )
        cfg = GlobalConfig()
        manifest = ManifestConfig(
            title=title,
            authors=author,
            description=description,
            data_sources=list(data),
            # Config defaults fill a new dataset; an update keeps the dataset's own.
            publisher=None if update else cfg.publisher,
            organization=None if update else cfg.organization,
        )
        agent = MDFAgent(root=None, manifest=manifest)

    elif manifest_path.exists():
        manifest_mode = True
        agent = MDFAgent.from_manifest(".")
        if title:
            agent.manifest.title = title
        if author:
            agent.manifest.authors = author
        if description:
            agent.manifest.description = description
        if not agent.manifest.data_sources and not update:
            agent.manifest.data_sources = ["."]

    elif update:
        # Metadata-only update driven by flags alone.
        agent = MDFAgent(
            root=None,
            manifest=ManifestConfig(title=title, authors=author, description=description),
        )

    else:
        _fail_usage(
            json_output,
            "No data paths provided and no mdf.yaml found",
            title="Nothing to publish",
            detail=(
                "Direct mode:    mdf publish ./data/ --title \"My Dataset\" --author \"Jane\" --submit\n"
                "Manifest mode:  mdf setup  →  mdf publish --submit"
            ),
            hint="run `mdf setup` to create an mdf.yaml in this directory",
        )

    if not update and agent.manifest.source_id:
        # mdf.yaml was published before: a plain publish would create a second
        # dataset (the server gives it a new id). Make that a choice, not an accident.
        known_id = agent.manifest.source_id
        if not dry_run and not preflight_only:
            _fail_usage(
                json_output,
                f"mdf.yaml is already published as {known_id}",
                title="Already published",
                hint=(
                    "add a version with `mdf publish --update --submit`, or delete "
                    "`source_id` from mdf.yaml to publish it as a new dataset"
                ),
            )
        chrome(json_output).print(
            f"[warn]mdf.yaml is already published as[/warn] {theme.ident(known_id)} "
            "[hint]— use --update to add a version[/hint]"
        )

    if update:
        from mdf.core.manifest import load_manifest

        cfg = GlobalConfig()
        # ID: the argument, else mdf.yaml (even when data paths were given),
        # else the last publish from this machine.
        manifest_id = agent.manifest.source_id or (
            load_manifest(manifest_path).source_id if manifest_path.exists() else None
        )
        target_id = update_id or manifest_id or cfg.last_source_id
        if not target_id:
            _fail_usage(
                json_output,
                "No dataset to update: no ID given, no source_id in mdf.yaml, no previous publish.",
                title="Nothing to update",
                hint="pass the ID: `mdf publish --update <source_id> --submit` (find it with `mdf list`)",
                detail=(
                    f"'{escape(data[0])}' exists on disk, so it was read as data, not as a dataset ID."
                    if data
                    else None
                ),
            )
        if not update_id:
            origin = "mdf.yaml" if manifest_id else "your last publish"
            chrome(json_output).print(
                f"[secondary]Updating {origin}:[/secondary] {theme.ident(target_id)}"
            )

        client = BackendClient.authenticated(
            base_url=api_url, token=token, service_instance=resolved, dev_user_id=dev_user
        )
        try:
            target_id = _resolve_identifier_with_notice(client, target_id, json_mode=json_output)
            with api_spinner("Loading the current version..."):
                previous = client.status(target_id)
        finally:
            client.close()
        if not is_success(previous):
            if json_output:
                json_fail(previous)
            require_success(previous, error_prefix=f"Could not load {target_id}")

        submission = previous.get("submission") or {}
        # Full metadata of the latest version, not the (partial) public card:
        # the card omits funding and related works.
        base = manifest_from_metadata(_parse_submission_mdata(submission))
        agent.manifest = merge_manifests(base, agent.manifest)
        agent.manifest.source_id = submission.get("source_id") or target_id
        if not agent.manifest.data_sources:
            agent.manifest.update_metadata_only = True

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
            if not preflight.get("success"):
                json_fail(
                    kind="validation",
                    message="Preflight found blocking issues",
                    hint="fix the reported issues, then re-run `mdf publish --preflight-only`",
                    extra={"preflight": preflight},
                )
            # Shape unchanged: --preflight-only has always emitted the
            # preflight report itself (it already carries `success`).
            json_ok(preflight)
            return
        _render_preflight(preflight)
        if not preflight.get("success"):
            raise typer.Exit(code=EXIT_USAGE)
        return

    if not preflight.get("success"):
        if json_output:
            json_fail(
                kind="validation",
                message="Publish blocked by preflight issues",
                hint="fix the reported issues, then re-run `mdf publish --preflight-only`",
                extra={"preflight": preflight},
            )
        _render_preflight(preflight)
        console.print()
        console.print(theme.error_panel(
            "Publish blocked by preflight issues",
            title="Blocked",
            hint="fix the ✗ items above, then re-run `mdf publish --preflight-only`",
        ))
        raise typer.Exit(code=EXIT_USAGE)

    try:
        payload = agent.build_submission(test=test, update=update)
    except (RuntimeError, ValueError) as exc:
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Could not build the submission",
            hint="fix the manifest (`mdf config validate`), then re-run `mdf publish`",
            kind="validation",
        )

    if dry_run:
        if json_output:
            emit_json({"success": True, "dry_run": True, "payload": payload, "preflight": preflight})
            return
        _render_preflight(preflight, title="Publish preflight")
        target = api_url or _api_url_for_service(resolved)
        console.print()
        console.print(theme.rule("Dry run — would update" if update else "Dry run — would submit"))
        console.print(f"[label]Target:[/label] {escape(str(target))} [secondary]({escape(resolved)})[/secondary]")
        if update:
            console.print(f"[label]Source ID:[/label] {theme.ident(agent.manifest.source_id)}")
        console.print()
        console.print(theme.json_syntax(payload))
        console.print()
        console.print(theme.panel(
            "[id]mdf publish --update --submit[/id]" if update else "[id]mdf publish --submit[/id]",
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
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Upload failed",
            hint="check connectivity with `mdf config doctor`, then retry `mdf publish --submit`",
        )

    published = is_success(result)
    source_id_val = result.get("source_id") if published else None
    version_val = result.get("version") if published else None
    saved_to_manifest = False
    if source_id_val:
        GlobalConfig().record_publish(source_id_val, version_val, resolved)
        if manifest_mode:
            saved_to_manifest = write_source_id(manifest_path, source_id_val)
    # Stderr in both modes, so the --json payload keeps its shape.
    if source_id_val and _targets_staging(resolved, api_url):
        err_console.print(f"[warn]{_STAGING_DOI_NOTICE}[/warn]", soft_wrap=True)

    if json_output:
        emit_json_result(result)
        return

    if published:
        detail = theme.detail_table(label_width=11)
        detail.add_row("Source ID", theme.ident(source_id_val))
        detail.add_row("Service", escape(str(resolved)))
        detail.add_row("Status", format_status_badge("pending_curation"))
        if version_val:
            detail.add_row("Version", theme.ident(version_val))
        console.print()
        console.print(theme.success_panel(
            detail,
            title="Updated successfully!" if update else "Published successfully!",
            emoji=theme.PACKAGE,
            subtitle="awaiting curation",
        ))
        if saved_to_manifest:
            console.print(
                "[hint]Saved source_id to mdf.yaml — `mdf publish --update --submit` "
                "adds a new version.[/hint]"
            )
        if not source_id_val:
            return
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
        require_success(result, error_prefix="Update failed" if update else "Publish failed")


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
    plan_only: bool = typer.Option(False, "--plan", "--dry-run", help="Show the clone plan without downloading"),
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
        mdf clone my_dataset_v1.1 --plan
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
            json_fail(plan, extra={"plan": plan})
    else:
        if not plan.get("success"):
            _render_clone_result(plan)
        headline = f"\n[bold]Cloning[/bold] {theme.ident(plan.get('resolved_source_id', source_id))}"
        if version:
            headline += f" [secondary]v{escape(str(version))}[/secondary]"
        console.print(headline)
        _render_clone_plan(plan)

    if plan_only:
        if json_output:
            json_ok({"plan": plan})
        return

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
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Clone failed",
            hint="try `mdf clone <id> --transfer` for cross-endpoint / Globus-only datasets",
        )

    if json_output:
        emit_json_result(result, {"plan": plan, "result": result}, extra={"plan": plan, "result": result})
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


def _abort_on_exception(
    exc: BaseException,
    *,
    json_output: bool,
    title: str,
    hint: str,
    kind: str = "network",
) -> None:
    """Turn an uncaught exception at a command boundary into the shared contract.

    Under ``--json`` stdout gets the one error envelope (never Rich markup);
    human mode keeps the familiar panel. Both exit with the code
    :data:`EXIT_CODE_BY_KIND` maps ``kind`` to, so the two modes never disagree
    about ``$?``. Never returns.
    """
    if isinstance(exc, AuthRequired):
        fail_auth_required(exc, json_mode=json_output)
    if json_output:
        json_fail(kind=kind, message=str(exc), hint=hint)
    console.print()
    console.print(theme.error_panel(
        escape(str(exc)),
        title=title,
        hint=hint,
    ))
    raise typer.Exit(code=EXIT_CODE_BY_KIND.get(kind, EXIT_ERROR))


def _add_id_column(table: Any, header: str, ids: List[Any]) -> None:
    """Add a first column that shows every identifier in full.

    IDs are what the next command needs, so they are never ellipsized: the
    column is as wide as the longest ID and does not wrap. On a terminal too
    narrow even for that, Rich folds the ID onto a second line rather than
    cutting it.
    """
    width = max([len(header)] + [len(str(value)) for value in ids if value])
    table.add_column(header, style="id", no_wrap=True, min_width=width, overflow="fold")


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


# ---------------------------------------------------------------------------
# Explore commands
# ---------------------------------------------------------------------------

@app.command("search", rich_help_panel=PANEL_EXPLORE)
def search(
    query: Optional[str] = typer.Argument(
        None, help="Search query (omit to browse the newest published datasets)"
    ),
    search_type: str = typer.Option("all", "--type", "-t", help="all, datasets, or streams"),
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
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    brief: bool = typer.Option(False, "--brief", "-b", help="Minimal JSON: title, authors, doi (requires --json)"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance (staging/prod/dev/local)"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id (X-User-Id)"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API base URL"),
):
    """Search public datasets and streams.

    The query is optional: with no query this browses the newest published
    datasets, in the same shape as a keyword search. Filter flags take whole
    facet values (as printed under "Facets" / returned in `facets`) and are
    repeatable — repeating one ORs the values together.

    Examples:
        mdf search "perovskite"
        mdf search                                  # browse the newest datasets
        mdf search "XRD" --type streams
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
    client = read_client(
        api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output
    )

    with api_spinner("Searching (semantic)..." if semantic else "Searching..."):
        if semantic:
            result = client.search_semantic(query, limit=limit)
            # Semantic search now requires auth (it spends the server's OpenAI key).
            # An anonymous caller gets a 401 {"detail": ...} — surface a login hint.
            detail = str(result.get("detail") or result.get("error") or "")
            if not result.get("results") and any(
                kw in detail.lower() for kw in ("auth", "token", "credential", "401", "unauthorized")
            ):
                client.close()
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
                client.close()
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
        else:
            result = client.search(
                query,
                search_type=search_type,
                limit=limit,
                offset=offset,
                sort=sort,
                **filters,
            )
    client.close()

    if json_output:
        if not is_success(result):
            json_fail(result)
        if brief:
            items = []
            # A backend that answers `"results": null` is an empty result set,
            # not a crash.
            for item in result.get("results") or []:
                entry = {
                    "title": item.get("title", ""),
                    "authors": item.get("authors", []),
                    "doi": item.get("doi", ""),
                }
                items.append(entry)
            # A bare list has nowhere to carry `success`; it stays a list so
            # existing --brief callers keep working.
            emit_json(items)
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
        table.add_column("Title", max_width=60)
        table.add_column("DOI", style="secondary", max_width=24, overflow="fold")

        for item in result["results"]:
            table.add_row(
                escape(str(item.get("source_id") or item.get("stream_id") or "")),
                escape(str(item.get("version") or "")),
                escape(str(item.get("title", "Untitled"))),
                escape(str(item.get("doi", ""))),
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
            console.print(f"\n[hint]Next: {' | '.join(next_steps)}[/hint]")
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


#: Hard stop on how many ``/submissions`` pages one `mdf list` will walk, so a
#: broad client-side ``--search`` can never turn into an unbounded crawl.
_LIST_MAX_PAGES = 20


def _parse_submission_mdata(sub: Dict[str, Any]) -> Dict[str, Any]:
    """Return a submission's ``dataset_mdata`` as a dict (it may arrive as JSON text)."""
    mdata = sub.get("dataset_mdata", {})
    if isinstance(mdata, str):
        try:
            mdata = json.loads(mdata)
        except Exception:
            mdata = {}
    return mdata if isinstance(mdata, dict) else {}


@app.command("list", rich_help_panel=PANEL_EXPLORE)
def list_datasets(
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
    status_filter: Optional[str] = typer.Option(
        None, "--status", help="Filter by status server-side (comma-separated for several)"
    ),
    search: Optional[str] = typer.Option(None, "--search", help="Filter by title, source ID, or DOI"),
    latest_only: bool = typer.Option(False, "--latest-only", help="Only show latest versions"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Override API URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Globus access token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user", help="Dev-mode user id"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """List your submitted datasets.

    `--limit` and `--status` are applied by the backend, and pages are followed
    via the response's `next_key` cursor until `--limit` records are collected —
    so a dataset outside the first page is still found. `--search` and
    `--latest-only` have no server-side equivalent and are applied to each page
    as it arrives.

    Under `--json` the echoed `next_key` is a safe resume point: if `--limit`
    filled up part-way through a page, it points at that same page (flagged
    `"partial_page": true`) rather than past the records that were never read.
    A page that fails mid-walk is reported as a failure (non-zero exit, error
    envelope) with whatever was already collected under `"partial": true`.

    Examples:
        mdf list
        mdf list --limit 50
        mdf list --status published,rejected
        mdf list --search perovskite --latest-only
    """
    resolved = resolve_service(service)
    client = BackendClient.authenticated(
        base_url=api_url,
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )

    wanted_statuses = {
        part.strip() for part in (status_filter or "").split(",") if part.strip()
    }
    query = (search or "").strip().lower()
    # `--search`/`--latest-only` have no server-side equivalent, so they need
    # material to filter: ask for full pages in that case. Otherwise the page
    # size is just what the caller asked for.
    client_side_filtering = bool(query or latest_only)
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
    client.close()

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
    _add_id_column(table, "Source ID", [sub.get("source_id") for sub, _ in filtered_submissions])
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
    if resume_key or partial_page:
        console.print(
            f"\n[hint]More records remain — raise --limit (currently {limit}) to fetch them.[/hint]"
        )


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
    client = read_client(
        api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output
    )
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
    client.close()

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
    _add_id_column(table, "Source ID", [row.get("source_id") for row in results])
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
    client = read_client(
        api_url=api_url, token=token, service=resolved, dev_user=dev_user, json_mode=json_output
    )
    source_id = _resolve_identifier_with_notice(client, source_id, json_mode=json_output)
    with api_spinner("Loading dataset..."):
        result = client.get_card(source_id, version=version)
        citation_result = None
        if cite and is_success(result):
            citation_result = client.get_citation(source_id, format="apa", version=version)
    client.close()

    if json_output:
        # The API response already is {"success", "card", "permissions"}: emit
        # it as-is (never nested under another "card"), plus the citation.
        output = dict(result)
        if citation_result:
            output["citation"] = citation_result
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
        # Chrome: under --json this goes to stderr so stdout stays parseable.
        chrome(json_output).print(theme.check_line(
            "active", "No mdf.yaml found — validating data paths only", indent=0
        ))
    else:
        message = "No mdf.yaml found and no data paths provided"
        hint = "run `mdf setup` to create a manifest, or pass data paths"
        if json_output:
            json_fail(
                kind="validation",
                message=message,
                hint=hint,
                extra={"errors": [message], "warnings": [], "preflight": None},
            )
        console.print()
        console.print(theme.error_panel(
            message,
            title="Nothing to validate",
            hint=hint,
        ))
        raise typer.Exit(code=EXIT_USAGE)

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
        if errors:
            json_fail(
                kind="validation",
                message=f"Validation failed: {len(errors)} blocking issue(s)",
                hint="fix the reported errors, then re-run `mdf validate --json`",
                extra={"errors": errors, "warnings": warnings, "preflight": preflight},
            )
        json_ok({"errors": errors, "warnings": warnings, "preflight": preflight})
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
        # Same code as `--json` (kind `validation`): blocking issues are a
        # usage error, not a generic failure.
        raise typer.Exit(code=EXIT_USAGE)

    console.print()
    console.print(theme.mark("ok", "Validation passed", style="ok.bold"))


@app.command(hidden=True)
def whoami(
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service instance"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show current authentication status (same as `mdf status --auth`)."""
    status(
        source_id=None,
        auth=True,
        watch=False,
        interval=10,
        timeout=1800,
        version=None,
        json_output=json_output,
        service=service,
        api_url=None,
        token=None,
        dev_user=None,
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
    """Watch a dataset until it reaches a terminal state (same as `mdf status --watch`)."""
    status(
        source_id=source_id,
        auth=False,
        watch=True,
        interval=interval,
        timeout=timeout,
        version=None,
        json_output=json_output,
        service=service,
        api_url=api_url,
        token=token,
        dev_user=dev_user,
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
    cite(source_id=source_id, format=format, output=None, copy=copy, version=version,
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
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json"),
    service: Optional[str] = typer.Option(None, "--service", "-s"),
    api_url: Optional[str] = typer.Option(None, "--api-url"),
    token: Optional[str] = typer.Option(None, "--token"),
    dev_user: Optional[str] = typer.Option(None, "--dev-user"),
):
    """Resubmit a rejected dataset."""
    from mdf.cli.dataset_cmd import resubmit
    resubmit(source_id=source_id, notes=notes, version=version, yes=yes,
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


# Hidden alias for `publish --update` (removed in the C8 clean break)
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
    """Update an existing published dataset (same as `mdf publish --update`)."""
    _run_publish(
        data=list(data or []),
        title=title,
        author=author,
        description=description,
        dry_run=dry_run,
        test=False,
        update=True,
        update_id=source_id,
        json_output=json_output,
        preflight_only=preflight_only,
        no_watch=no_watch,
        service=service,
        api_url=api_url,
        token=token,
        dev_user=dev_user,
    )


# ---------------------------------------------------------------------------
# Import (cross-publish from external repos)
# ---------------------------------------------------------------------------

@app.command("import", rich_help_panel=PANEL_PUBLISH)
def import_external(
    identifier: str = typer.Argument(..., help="External identifier (Zenodo record or DOI)"),
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
    """Import a dataset from an external repository or a DataCite DOI.

    Fetches metadata and data files, then publishes to MDF with
    provenance linking back to the original source.

    Examples:
        mdf import zenodo:12345                    # preview metadata
        mdf import 10.18126/e73h-3w6n              # preview DataCite metadata
        mdf import zenodo:12345 --submit           # download + submit
        mdf import zenodo:12345 -o ./data --submit # custom output dir
    """
    from mdf.importers.registry import resolve_adapter

    adapter = resolve_adapter(identifier)
    if not adapter:
        if json_output:
            json_fail(
                kind="validation",
                message=f"No adapter found for: {identifier}",
                hint="use a DOI, zenodo:ID, or a public Zenodo record URL",
            )
        console.print()
        console.print(theme.error_panel(
            f"No adapter found for: {escape(str(identifier))}",
            title="Unsupported source",
            detail="Supported formats: DOI, doi:DOI, https://doi.org/DOI, zenodo:ID, Zenodo record URL",
            hint="check the identifier, or open an issue to request a new repository adapter",
        ))
        raise typer.Exit(code=1)

    # Fetch metadata
    with api_spinner(f"Fetching metadata from {adapter.name()}…"):
        try:
            metadata = adapter.fetch_metadata(identifier)
        except Exception as exc:
            _abort_on_exception(
                exc,
                json_output=json_output,
                title="Failed to fetch metadata",
                hint=f"confirm the record exists and is public on {adapter.name()}",
            )

    # Show metadata panel
    if not json_output:
        table = theme.detail_table(label_width=12)
        table.add_row("Source", theme.ident(adapter.name()))
        table.add_row("Title", escape(str(metadata.get("title", ""))))
        table.add_row("Authors", escape(format_authors(metadata.get("authors"))))
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
            emit_json({"success": True, "dry_run": True, "metadata": metadata})
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
            import_metadata = dict(metadata)
            provenance = import_metadata.get("external_source")
            if isinstance(provenance, dict):
                import_metadata["external_source"] = provenance.get("source") or adapter.name()
                import_metadata.setdefault("doi", provenance.get("doi"))
                import_metadata.setdefault("url", provenance.get("url"))
            result = agent.import_external(
                identifier=identifier,
                output_dir=output_dir,
                metadata=import_metadata,
                progress_callback=download.on_progress,
                on_files_resolved=download.on_files_resolved,
                on_file_done=download.on_file_done,
            )
    except Exception as exc:
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Import failed",
            hint=f"retry, or download from {adapter.name()} manually and use `mdf publish`",
        )

    if not result.get("success"):
        if json_output:
            json_fail(result)
        console.print()
        console.print(theme.error_panel(
            escape(str(result.get("error", "Unknown error"))),
            title="Import failed",
            hint=f"retry, or download from {adapter.name()} manually and use `mdf publish`",
        ))
        raise typer.Exit(code=EXIT_ERROR)

    # Now publish using the generated manifest (includes upload to MDF storage)
    manifest = result["manifest"]
    manifest.publication_year = metadata.get("publication_year")
    manifest.license = metadata.get("license")
    manifest.related_works = metadata.get("related_works")
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
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Upload failed",
            hint="the download is kept — re-run `mdf publish --submit` from the output directory",
        )

    if json_output:
        emit_json_result(
            pub_result,
            {"import": result, "publish": pub_result},
            extra={"import": result, "publish": pub_result},
        )
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
