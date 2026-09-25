"""`mdf status [ID] [--watch]`."""

from __future__ import annotations

from typing import Optional

import typer

from mdf.cli import theme
from mdf.cli.app import PANEL_PUBLISH, app
from mdf.cli.commands._shared import (
    _render_status_details,
    _resolve_identifier_with_notice,
    _watch_submission,
)
from mdf.cli.formatting import (
    EXIT_USAGE,
    api_spinner,
    console,
    emit_json_result,
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
from mdf.core.config import GlobalConfig, resolve_service


@app.command(rich_help_panel=PANEL_PUBLISH)
def status(
    source_id: Optional[str] = typer.Argument(None, help="Source ID to check (default: last published)"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Poll until terminal state"),
    interval: int = typer.Option(10, "--interval", "-i", help="Poll interval in seconds (with --watch)"),
    timeout: int = typer.Option(1800, "--timeout", help="Timeout in seconds (with --watch)"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Dataset version"),
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Check a submission's status (default: your last publish).

    With --watch: polls until the submission reaches a terminal state.
    """
    resolved = resolve_service(service)

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
        with open_client(public=False, api_url=api_url, token=token, service=resolved, dev_user=dev_user) as client:
            lookup_id = _resolve_identifier_with_notice(client, lookup_id, json_mode=json_output)

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
    with open_client(public=False, api_url=api_url, token=token, service=resolved, dev_user=dev_user) as client:
        lookup_id = _resolve_identifier_with_notice(client, lookup_id, json_mode=json_output)
        with api_spinner("Checking status..."):
            result = client.status(lookup_id, version=version)

    if json_output:
        emit_json_result(result)
        return

    if not is_success(result):
        # A 404/401/500 is not a status: report it with the mapped exit code
        # instead of rendering an empty panel and exiting 0.
        require_success(result, error_prefix="Status failed")

    _render_status_details(result, lookup_id)
