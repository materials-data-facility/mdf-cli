"""``mdf mcp`` — run and install the MDF Model Context Protocol server.

``mdf mcp serve`` starts the read-only MCP server on stdio (see
:mod:`mdf.mcp.server`); ``mdf mcp install`` prints the JSON snippet an MCP
client needs in order to launch it. ``install`` deliberately **prints** rather
than edits: an assistant's config file is the user's, and hand-editing a JSON
file we do not own is how configs get clobbered.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.markup import escape

from mdf.cli import theme
from mdf.cli.formatting import EXIT_ERROR, EXIT_USAGE, err_console

#: MCP clients and where their config lives. All three read the same
#: ``mcpServers`` object; only the file (and how it is edited) differs.
CLIENTS: Dict[str, Dict[str, str]] = {
    "claude-code": {
        "label": "Claude Code",
        "path": ".mcp.json (project root) or ~/.claude.json (user scope)",
        "note": "or run: claude mcp add-json mdf '<the JSON object under \"mdf\">'",
    },
    "claude-desktop": {
        "label": "Claude Desktop",
        "path": (
            "~/Library/Application Support/Claude/claude_desktop_config.json (macOS)"
            " · %APPDATA%\\Claude\\claude_desktop_config.json (Windows)"
        ),
        "note": "merge into the existing file, then restart Claude Desktop",
    },
    "cursor": {
        "label": "Cursor",
        "path": "~/.cursor/mcp.json (global) or .cursor/mcp.json (per project)",
        "note": "merge into the existing file, then reload Cursor",
    },
}

SERVER_KEY = "mdf"


app = typer.Typer(
    help="Model Context Protocol server for AI assistants (read-only)",
    no_args_is_help=True,
)


def _launch_command() -> Tuple[str, List[str]]:
    """The command an MCP client should run to start this server.

    Prefers the ``mdf`` console script installed beside the running interpreter
    — an absolute path, because GUI clients (Claude Desktop, Cursor) do not
    inherit a shell PATH — then anything on PATH, then ``python -m mdf``.
    """
    beside = Path(sys.executable).parent / ("mdf.exe" if os.name == "nt" else "mdf")
    if beside.exists():
        return str(beside), ["mcp", "serve"]
    on_path = shutil.which("mdf")
    if on_path:
        return on_path, ["mcp", "serve"]
    return sys.executable, ["-m", "mdf", "mcp", "serve"]


def _server_env(service: Optional[str], api_url: Optional[str]) -> Dict[str, str]:
    """Pin a service in the snippet only when the user asked for one.

    Without ``--service``/``--api-url`` the snippet carries no ``MDF_SERVICE``,
    so the server follows the CLI's own default — and follows it when the
    release default moves from staging to prod, instead of staying pinned.
    """
    if api_url:
        return {"MDF_API_URL": api_url}
    if service:
        return {"MDF_SERVICE": service}
    return {}


def config_snippet(
    client: str = "claude-code",
    service: Optional[str] = None,
    api_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the ``{"mcpServers": {...}}`` config object for ``client``."""
    command, args = _launch_command()
    env = _server_env(service, api_url)
    return {
        "mcpServers": {
            SERVER_KEY: {
                "command": command,
                "args": args,
                **({"env": env} if env else {}),
            }
        }
    }


@app.command("serve")
def serve(
    service: Optional[str] = typer.Option(
        None, "--service", "-s", help="Service instance (staging/prod/dev/local)"
    ),
    api_url: Optional[str] = typer.Option(
        None, "--api-url", help="Override API base URL (wins over --service)"
    ),
):
    """Run the MDF MCP server on stdio.

    Exposes read-only tools — search_datasets, get_dataset_card, get_citation,
    list_files, get_sample, get_versions, resolve_doi — over the public MDF API.
    The server is anonymous: it never logs in and cannot publish, edit or
    curate anything.

    Started by an MCP client, not usually by hand: stdin/stdout carry the
    protocol. Print a client config with `mdf mcp install`.

    Examples:
        mdf mcp serve
        mdf mcp serve --service prod
    """
    try:
        from mdf.mcp import server as mcp_server
    except ImportError as exc:  # pragma: no cover - mdf.mcp has no hard deps
        err_console.print(theme.error_panel(escape(str(exc)), title="MCP server unavailable"))
        raise typer.Exit(code=EXIT_ERROR)

    try:
        mcp_server.run_server(service=service, api_url=api_url)
    except ImportError as exc:
        # The `mcp` SDK is an optional extra; say so instead of a traceback.
        err_console.print(
            theme.error_panel(
                escape(str(exc)),
                title="Missing dependency",
                hint='install the extra: pip install "mdf-cli[mcp]"',
            )
        )
        raise typer.Exit(code=EXIT_ERROR)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        raise typer.Exit(code=0)


@app.command("install")
def install(
    client: str = typer.Option(
        "claude-code",
        "--client",
        "-c",
        help="MCP client: claude-code, claude-desktop, or cursor",
    ),
    service: Optional[str] = typer.Option(
        None, "--service", "-s", help="Service instance to pin in the snippet"
    ),
    api_url: Optional[str] = typer.Option(
        None, "--api-url", help="Pin an explicit API base URL instead of a service"
    ),
):
    """Print the MCP client config for the MDF server (does not edit any file).

    stdout is the JSON object only, so it pipes:

        mdf mcp install --client claude-code > .mcp.json
        mdf mcp install --client cursor | jq .

    Where it goes, and the client's own one-liner, are printed to stderr.
    """
    if client not in CLIENTS:
        err_console.print(
            theme.error_panel(
                f"Unknown client: {escape(str(client))}",
                title="Unknown client",
                hint=f"choose one of: {', '.join(CLIENTS)}",
            )
        )
        raise typer.Exit(code=EXIT_USAGE)

    snippet = config_snippet(client=client, service=service, api_url=api_url)
    # stdout: byte-plain JSON, nothing else. All chrome goes to stderr.
    print(json.dumps(snippet, indent=2))

    info = CLIENTS[client]
    err_console.print(
        f"\n[secondary]{escape(info['label'])} config:[/secondary] "
        f"[id]{escape(info['path'])}[/id]"
    )
    err_console.print(f"[hint]{escape(info['note'])}[/hint]")
    try:
        import mcp  # noqa: F401
    except ImportError:
        err_console.print(
            '[hint]the server needs the MCP extra: pip install "mdf-cli[mcp]"[/hint]'
        )
