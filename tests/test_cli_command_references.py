"""Every `mdf …` command named in the code, README and bundled skills exists (CLI-U7).

Hints, docs and the agent skill used to point at hidden aliases and at commands
that never existed (`mdf config validate`). This walks the real click tree and
fails on any `mdf <command> [<subcommand>]` reference that does not resolve.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

import typer

from mdf.cli.main import app

REPO = Path(__file__).resolve().parents[1]
SOURCES = sorted(
    [p for p in (REPO / "src" / "mdf").rglob("*") if p.suffix in (".py", ".md")]
    + [REPO / "README.md"]
)
#: `mdf <word> [<word>]`, not part of a longer token (mdf.yaml, mdf-cli, /mdf_open).
REFERENCE = re.compile(r"(?<![\w./@-])mdf ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")


def _references() -> List[Tuple[str, str, str]]:
    found = []
    for path in SOURCES:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in REFERENCE.finditer(line):
                found.append((f"{path.relative_to(REPO)}:{number}", match.group(1), match.group(2) or ""))
    return found


def test_references_were_found():
    assert len(_references()) > 50  # guard the scanner itself


def test_every_mdf_command_reference_resolves():
    root = typer.main.get_command(app)
    unresolved = []
    for where, command, sub in _references():
        target = root.commands.get(command)
        if target is None:
            unresolved.append(f"{where}: mdf {command}")
            continue
        subcommands = getattr(target, "commands", None)
        if subcommands and sub and sub not in subcommands:
            unresolved.append(f"{where}: mdf {command} {sub}")
    assert not unresolved, "unknown commands referenced:\n" + "\n".join(unresolved)
