"""`--help` snapshots for the top-level app and every sub-app (plan C3).

The help text is the CLI's front door and its command inventory; a snapshot
makes every change to it (a renamed flag, a vanished command, a reordered
panel) show up in review.

Regenerate after an intended change:

    MDF_UPDATE_HELP_SNAPSHOTS=1 .venv/bin/python -m pytest tests/test_cli_help_snapshots.py -p no:xdist
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import pytest
import typer
from typer.testing import CliRunner

from mdf.cli.main import app

SNAPSHOTS = Path(__file__).parent / "fixtures" / "help"
UPDATE = os.environ.get("MDF_UPDATE_HELP_SNAPSHOTS") == "1"
#: Hidden dev groups scheduled for deletion (CLI-D4) are not snapshotted.
EXCLUDED_GROUPS = {"backend", "stream"}

runner = CliRunner()


def _groups() -> List[List[str]]:
    found: List[List[str]] = []

    def walk(command, path: List[str]) -> None:
        subcommands = getattr(command, "commands", None)
        if not subcommands:
            return
        found.append(path)
        for name, sub in sorted(subcommands.items()):
            if not path and name in EXCLUDED_GROUPS:
                continue
            walk(sub, path + [name])

    walk(typer.main.get_command(app), [])
    return found


GROUPS = _groups()


def _snapshot_name(path: List[str]) -> str:
    return "mdf" + "".join(f"_{part}" for part in path) + ".txt"


def test_every_visible_sub_app_is_covered():
    covered = {tuple(path) for path in GROUPS}
    assert {(), ("dataset",), ("admin",), ("config",), ("skill",), ("mcp",)} <= covered


@pytest.mark.parametrize("path", GROUPS, ids=lambda path: " ".join(["mdf", *path]))
def test_help_snapshot(path):
    result = runner.invoke(app, [*path, "--help"], env={"COLUMNS": "100", "TERMINAL_WIDTH": "100"})
    assert result.exit_code == 0, result.output
    text = "\n".join(line.rstrip() for line in result.stdout.splitlines()) + "\n"
    snapshot = SNAPSHOTS / _snapshot_name(path)
    if UPDATE or not snapshot.exists():
        if not UPDATE:
            pytest.fail(
                f"missing snapshot {snapshot.name}; create it with MDF_UPDATE_HELP_SNAPSHOTS=1"
            )
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(text)
    assert text == snapshot.read_text(), (
        f"`mdf {' '.join(path)} --help` changed; review it and regenerate with "
        "MDF_UPDATE_HELP_SNAPSHOTS=1 if intended"
    )
