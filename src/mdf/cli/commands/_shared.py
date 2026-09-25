"""Helpers shared by several `mdf` commands."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import typer
from rich.markup import escape
from rich.text import Text

from mdf.cli import theme
from mdf.cli.formatting import (
    EXIT_CODE_BY_KIND,
    EXIT_ERROR,
    EXIT_USAGE,
    console,
    fail_auth_required,
    format_status_badge,
    json_fail,
    resolve_identifier_with_notice,
)
from mdf.cli.options import open_client
from mdf.core.backend_client import BackendClient
from mdf.core.exceptions import AuthRequired

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
            console.print(f"  [hint]Try: mdf show {sid}  ·  mdf cite {sid}  ·  mdf dataset open {sid}[/hint]")
        elif st == "publish_failed":
            console.print("  [warn]Next:[/warn] Approved, but the search ingest failed — not yet live. The pipeline retries; if it persists, contact a curator.")
            console.print(f"  [hint]Try: mdf status --watch {sid}[/hint]")
        elif st == "rejected":
            console.print("  [label]Next:[/label] Review feedback, edit metadata if needed, then resubmit")
            console.print(f"  [hint]Try: mdf dataset edit {sid} --title \"Updated title\"  ·  mdf dataset resubmit {sid}[/hint]")
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
            with open_client(public=False, api_url=api_url, token=token, service=service, dev_user=dev_user) as client:
                result = client.status(lookup_id)
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



def _or_dash(value: Any) -> str:
    """A table cell for a possibly-missing value: ``—`` instead of ``None``/empty."""
    return "—" if value is None or str(value).strip() in ("", "None") else str(value)



def _one_line(text: Any) -> Text:
    """A table cell for a title: whitespace collapsed, one line, ``…`` when cut."""
    return Text(" ".join(str(text or "").split()) or "Untitled", no_wrap=True, overflow="ellipsis")


def _add_title_column(table: Any) -> None:
    """A last column that takes the remaining width and ellipsizes (never wraps)."""
    # The column itself may shrink (so Rich never drops other columns to fit);
    # each cell is a no-wrap Text that ellipsizes instead of wrapping.
    table.add_column("Title", ratio=1, min_width=12)



def _parse_submission_mdata(sub: Dict[str, Any]) -> Dict[str, Any]:
    """Return a submission's ``dataset_mdata`` as a dict (it may arrive as JSON text)."""
    mdata = sub.get("dataset_mdata", {})
    if isinstance(mdata, str):
        try:
            mdata = json.loads(mdata)
        except Exception:
            mdata = {}
    return mdata if isinstance(mdata, dict) else {}


#: Byte formatting lives with the rest of the visual language.
_human_size = theme.human_size
