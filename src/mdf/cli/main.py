"""`mdf` — the Materials Data Facility command-line interface (entry point).

The app and its login policy live in :mod:`mdf.cli.app`; each area's commands
live in :mod:`mdf.cli.commands` and register themselves on import. This module
assembles the tree:

    Get started   login · logout · whoami · init [PATH] · doctor
    Find & reuse  search · show · cite · clone · related · versions
    Publish       check · publish · status · list · import
    Manage        dataset edit|withdraw|resubmit|diff|open
    More          admin · config · manifest · skill · mcp
"""

from __future__ import annotations

from mdf.cli.app import PANEL_AUTH, PANEL_EXPLORE, PANEL_MANAGE, PANEL_MORE, app
from mdf.cli.commands import (  # noqa: F401  (imported to register their commands)
    auth,
    clone,
    import_,
    list as list_,
    publish,
    search,
    show,
    status,
)
from mdf.cli.admin_cmd import app as admin_app
from mdf.cli.config_cmd import app as config_app, doctor as doctor_command
from mdf.cli.dataset_cmd import app as dataset_app, cite as cite_command, versions as versions_command
from mdf.cli.manifest_cmd import app as manifest_app, init as init_command
from mdf.cli.mcp_cmd import app as mcp_app
from mdf.cli.skill_cmd import app as skill_app

# Top-level commands that live with related code in other modules.
app.command("init", rich_help_panel=PANEL_AUTH)(init_command)
app.command("doctor", rich_help_panel=PANEL_AUTH)(doctor_command)
app.command("cite", rich_help_panel=PANEL_EXPLORE)(cite_command)
app.command("versions", rich_help_panel=PANEL_EXPLORE)(versions_command)

# Sub-apps
app.add_typer(dataset_app, name="dataset", rich_help_panel=PANEL_MANAGE)
app.add_typer(admin_app, name="admin", rich_help_panel=PANEL_MORE)
app.add_typer(config_app, name="config", rich_help_panel=PANEL_MORE)
app.add_typer(manifest_app, name="manifest", rich_help_panel=PANEL_MORE)
app.add_typer(skill_app, name="skill", rich_help_panel=PANEL_MORE)
app.add_typer(mcp_app, name="mcp", help="Model Context Protocol server for AI assistants", rich_help_panel=PANEL_MORE)

#: `mdf --help` lists commands in this order within each panel (Typer uses
#: registration order, which follows import order).
_HELP_ORDER = [
    "login", "logout", "whoami", "init", "doctor",
    "search", "show", "cite", "clone", "related", "versions",
    "check", "publish", "status", "list", "import",
]
app.registered_commands.sort(
    key=lambda info: _HELP_ORDER.index(info.name or info.callback.__name__)
    if (info.name or info.callback.__name__) in _HELP_ORDER
    else len(_HELP_ORDER)
)


if __name__ == "__main__":
    app()
