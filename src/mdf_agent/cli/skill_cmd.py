"""MDF agent-skill subcommands.

Provides `mdf skill install`, `mdf skill path`, and `mdf skill list` for
installing the packaged agent skill bundles (currently `mdf-publish`) into an
agent runtime's skills directory.

Note: this is unrelated to `mdf_agent.skill` (singular), which is the
in-process tool registry used by MCP-style hosts.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Sequence

import typer
from rich.console import Console
from rich.table import Table

console = Console()

# Bundles shipped inside the wheel under src/mdf_agent/skills/
BUNDLED_SKILLS = ["mdf-publish"]

DEFAULT_SKILL = "mdf-publish"
AGENTS = ("claude", "codex", "gemini")


app = typer.Typer(
    help="Install the packaged MDF agent skills (Claude, Codex, Gemini)",
    no_args_is_help=True,
)


def _bundle_root() -> Path:
    """Locate the packaged skills/ directory (works from a wheel or a checkout)."""
    from importlib.resources import files

    return Path(str(files("mdf_agent") / "skills"))


def _bundle_path(name: str) -> Path:
    return _bundle_root() / name


def _copy_bundle(source: Path, destination: Path) -> List[Path]:
    """Copy a skill bundle, returning the list of files written."""
    written: List[Path] = []
    for item in sorted(source.rglob("*")):
        if item.is_dir() or "__pycache__" in item.parts:
            continue
        target = destination / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        written.append(target)
    return written


def _selected_agents(agent: Optional[Sequence[str]]) -> List[str]:
    """Normalize repeated --agent options and validate the all shorthand."""
    selected = list(agent or ["claude"])
    if "all" in selected:
        if len(selected) != 1:
            raise typer.BadParameter("--agent all cannot be combined with another agent")
        return list(AGENTS)
    invalid = [item for item in selected if item not in AGENTS]
    if invalid:
        raise typer.BadParameter(
            f"Unknown agent: {invalid[0]}. Choose claude, codex, gemini, or all."
        )
    return list(dict.fromkeys(selected))


def _default_destination(agent: str, name: str, project: bool) -> Path:
    """Resolve an agent's conventional global or project-level skill path."""
    if project:
        if agent != "claude":
            raise typer.BadParameter(
                "--project is currently supported only for --agent claude "
                "(.claude/skills/); use a global install for this agent"
            )
        base = Path.cwd() / ".claude" / "skills"
    else:
        base = Path.home() / f".{agent}" / "skills"
    return (base / name).resolve()


@app.command("install")
def skill_install(
    name: str = typer.Argument(DEFAULT_SKILL, help="Skill bundle to install"),
    project: bool = typer.Option(
        False, "--project", help="Install into ./.claude/skills/ instead of ~/.claude/skills/"
    ),
    agent: Optional[List[str]] = typer.Option(
        None,
        "--agent",
        help="Agent runtime: claude, codex, gemini, or all (repeat for multiple agents)",
    ),
    target: Optional[str] = typer.Option(
        None,
        "--target",
        help="Explicit destination directory (only with one --agent)",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing installation"),
):
    """Install a packaged MDF skill into your coding agent.

    Examples:
        mdf skill install
        mdf skill install --project
        mdf skill install --agent codex
        mdf skill install --agent all
        mdf skill install --agent gemini --target ~/.gemini/skills/mdf-publish
    """
    if name not in BUNDLED_SKILLS:
        console.print(f"[red]Unknown skill:[/red] {name}")
        console.print(f"[dim]Available:[/dim] {', '.join(BUNDLED_SKILLS)}")
        raise typer.Exit(code=1)

    source = _bundle_path(name)
    if not source.is_dir():
        console.print(f"[red]Packaged skill not found:[/red] {source}")
        console.print("[dim]The installed mdf-cli build may be missing its skill data.[/dim]")
        raise typer.Exit(code=1)

    selected = _selected_agents(agent)
    if target and len(selected) != 1:
        raise typer.BadParameter("--target can only be used with one agent")
    if project and any(item != "claude" for item in selected):
        raise typer.BadParameter(
            "--project is currently supported only for --agent claude "
            "(.claude/skills/); use a global install for this agent"
        )

    failures = False
    for selected_agent in selected:
        destination = (
            Path(target).expanduser().resolve()
            if target
            else _default_destination(selected_agent, name, project)
        )

        if destination.exists() and any(destination.iterdir()) and not force:
            console.print(f"[yellow]Already installed ({selected_agent}):[/yellow] {destination}")
            console.print("[dim]Re-run with [/dim][cyan]--force[/cyan][dim] to overwrite.[/dim]")
            failures = True
            continue

        destination.mkdir(parents=True, exist_ok=True)
        _copy_bundle(source, destination)
        console.print(
            f"[green]Installed[/green] [bold]{name}[/bold] for [bold]{selected_agent}[/bold] "
            f"[dim]->[/dim] {destination}; invoke with [cyan]/mdf-publish[/cyan]"
        )

    if failures:
        raise typer.Exit(code=1)


@app.command("path")
def skill_path(
    name: str = typer.Argument(DEFAULT_SKILL, help="Skill bundle name"),
):
    """Print the location of a packaged skill bundle."""
    if name not in BUNDLED_SKILLS:
        console.print(f"[red]Unknown skill:[/red] {name}")
        raise typer.Exit(code=1)
    print(_bundle_path(name))


@app.command("list")
def skill_list():
    """List the skill bundles shipped with mdf-cli."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Skill")
    table.add_column("Packaged", style="dim")
    for name in BUNDLED_SKILLS:
        path = _bundle_path(name)
        table.add_row(name, str(path) if path.is_dir() else "[red]missing[/red]")
    console.print(table)
    console.print("\n[dim]Install with:[/dim] [cyan]mdf skill install[/cyan]")
