"""`mdf clone ID [DIR] [--plan [--files]]`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich.markup import escape

from mdf.cli import theme
from mdf.cli.app import PANEL_EXPLORE, app
from mdf.cli.commands._shared import (
    _abort_on_exception,
    _human_size,
)
from mdf.cli.formatting import (
    console,
    emit_json_result,
    json_fail,
    json_ok,
)
from mdf.cli.options import ApiUrlOpt, DevUserOpt, JsonOpt, ServiceOpt, TokenOpt
from mdf.core.agent import MDFAgent
from mdf.core.config import resolve_service

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
                f"\n[hint]Next: cd {escape(str(result['path']))}  ·  mdf show {escape(str(source_id))}[/hint]",
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



@app.command(rich_help_panel=PANEL_EXPLORE)
def clone(
    source_id: str = typer.Argument(..., help="Source dataset ID or DOI to download"),
    output_dir: Optional[str] = typer.Argument(None, help="Output directory (default: ./{source_id})"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Specific version to clone"),
    transfer: bool = typer.Option(False, "--transfer", help="Use Globus Transfer (requires GCP)"),
    derive: bool = typer.Option(False, "--derive", help="Create mdf.yaml with derived-from lineage"),
    plan_only: bool = typer.Option(False, "--plan", "--dry-run", help="Show the clone plan without downloading"),
    list_files: bool = typer.Option(
        False, "--files", help="With --plan: also list every file (datasets without an archive need a login)"
    ),
    listing_timeout: float = typer.Option(
        300, "--timeout", help="Give up listing files after this many seconds"
    ),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
    api_url: ApiUrlOpt = None,
):
    """Download a dataset from MDF.

    Downloads using the fastest available method: zip archive (if available),
    HTTPS file-by-file, or Globus Transfer (with --transfer flag).

    Examples:
        mdf clone my_dataset_v1.1
        mdf clone my_dataset_v1.1 ./local_copy
        mdf clone my_dataset_v1.1 --transfer
        mdf clone my_dataset_v1.1 --derive
        mdf clone my_dataset_v1.1 --plan            # reads the dataset card only
        mdf clone my_dataset_v1.1 --plan --files    # also lists every file
    """
    from mdf.core.agent import CloneListingError

    resolved = resolve_service(service)
    method = "transfer" if transfer else "auto"

    agent = MDFAgent()

    resolved_token = token or os.environ.get("MDF_CONNECT_TOKEN")
    resolved_dev_user = dev_user or os.environ.get("MDF_DEV_USER_ID")

    # Default output dir: ./{source_id} — use the last path component of a DOI
    if output_dir is None:
        dir_name = source_id.rstrip("/").split("/")[-1]
        output_dir = dir_name

    try:
        with theme.spinner("Reading the dataset card…") as update:
            plan = agent.plan_clone(
                source_id=source_id,
                output_dir=output_dir,
                version=version,
                method=method,
                api_url=api_url,
                token=resolved_token,
                service_instance=resolved,
                dev_user_id=resolved_dev_user,
                list_files=plan_only and list_files,
                on_listing=lambda count: update(f"Listing files… {count}"),
                listing_timeout=listing_timeout,
            )
    except CloneListingError as exc:
        _abort_on_exception(
            exc,
            json_output=json_output,
            title="Could not list the dataset's files",
            hint="retry, raise --timeout, or drop --files for a card-only plan",
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
        for rel_path in plan.get("files") or []:
            console.print(f"  {escape(str(rel_path))}", soft_wrap=True)

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
