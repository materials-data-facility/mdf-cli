"""The root `mdf` Typer app: login policy, help panels, first-run screen."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
import typer.rich_utils as typer_rich_utils
from rich import box as rich_box
from rich.panel import Panel as RichPanel

try:  # Typer >= 0.26 vendors click; its UsageError is what bad flags/values raise.
    from typer._click.exceptions import UsageError
except ImportError:  # older Typer uses click itself
    from click.exceptions import UsageError
from typer.core import TyperGroup

from mdf.auth.globus import interactive_login
from mdf.cli import theme
from mdf.cli.formatting import (
    EXIT_USAGE,
    console,
    fail_auth_required,
    json_fail,
    login_allowed,
)
from mdf.core.exceptions import AuthRequired
from mdf.version import __version__


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
            except UsageError as exc:
                # Bad flags or values (unknown option, bad choice, missing
                # argument). Human mode keeps click's own message; --json gets
                # the envelope, so stdout is JSON on every exit path.
                if not json_mode:
                    raise
                json_fail(
                    kind="validation",
                    message=exc.format_message(),
                    hint="see `mdf <command> --help`",
                    exit_code=EXIT_USAGE,
                )



app = typer.Typer(
    cls=MDFGroup,
    help="MDF CLI — Materials Data Facility",
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



PANEL_AUTH = "Get started"


PANEL_AUTH = "Get started"
PANEL_EXPLORE = "Find & reuse"


PANEL_EXPLORE = "Find & reuse"
PANEL_PUBLISH = "Publish"


PANEL_PUBLISH = "Publish"
PANEL_MANAGE = "Manage"


PANEL_MANAGE = "Manage"
PANEL_MORE = "More"



def _help_panel(*args, **kwargs):
    kwargs.setdefault("box", rich_box.SIMPLE)
    return RichPanel(*args, **kwargs)



typer_rich_utils.Panel = _help_panel


# Help output is the CLI's front door: give it the same accent as everything else.
theme.style_typer_help()



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
    """MDF CLI — Materials Data Facility"""
    if ctx.invoked_subcommand is not None:
        return

    # First-run: no config file yet
    cfg_path = Path.home() / ".config" / "mdf" / "config.json"
    if not cfg_path.exists():
        console.print(_LOGO)
        console.print(theme.panel(
            "[secondary]Publish, find and reuse materials data.[/secondary]\n"
            "\n"
            "[bold]Find data[/bold]\n"
            '  [id]mdf search "perovskite"[/id]   Find datasets\n'
            "  [id]mdf show <id>[/id]             Metadata, citation, files\n"
            "  [id]mdf clone <id>[/id]            Download a dataset\n"
            "\n"
            "[bold]Publish[/bold]\n"
            "  [id]mdf login[/id]                 Sign in with Globus\n"
            "  [id]mdf init[/id]                  Create mdf.yaml for your data\n"
            "  [id]mdf check[/id]                 Run the submit-time checks\n"
            "  [id]mdf publish --submit[/id]      Send it to MDF for curation\n"
            "\n"
            "[bold]Agents[/bold]\n"
            "  [id]mdf skill install[/id]         Install the publishing skill\n"
            "  [id]mdf mcp install[/id]           Connect an MCP client (Claude, Cursor)\n"
            "\n"
            "[secondary]For scripts/CI: set[/secondary] [id]MDF_CLIENT_ID[/id] [secondary]+[/secondary] [id]MDF_CLIENT_SECRET[/id]\n"
            "\n"
            "[hint]hint: run `mdf --help` for all commands[/hint]",
            title=f"{theme.SCIENCE} MDF CLI",
            subtitle=f"v{__version__}",
        ))
    else:
        # Show help
        console.print(_LOGO)
        console.print(ctx.get_help())
