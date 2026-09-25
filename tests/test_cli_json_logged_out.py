"""Every `--json` command, logged out with no stdin, keeps the JSON contract (plan C3).

Each command runs with an empty ``HOME`` (no config, no Globus tokens, no saved
token), no ``MDF_*`` credentials, stdin that is not a TTY, and an API URL on a
closed local port — so nothing can reach a real service. It must then:

* print exactly one parseable JSON document on stdout,
* exit 0, 2, 3, 4 or 5 (never 1, never a traceback),
* never prompt (a login prompt would show up on stdout/stderr or hang).

The commands run inside one child process: the Globus token path is resolved
from ``HOME`` at import time, so an isolated ``HOME`` needs a fresh
interpreter, and one interpreter keeps the test fast.

"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest
import typer

from mdf.cli.main import app

EXCLUDED_GROUPS: set = set()
ALLOWED_EXIT_CODES = {0, 2, 3, 4, 5}
PROMPT_MARKERS = ("Visit this URL", "Authorization Code", "Please authenticate with Globus")


def _json_commands() -> List[Tuple[str, List[str]]]:
    """(id, argv) for every leaf command with a --json flag, placeholders filled in."""
    found: List[Tuple[str, List[str]]] = []

    def walk(command, path: List[str]) -> None:
        subcommands = getattr(command, "commands", None)
        if subcommands:
            for name, sub in sorted(subcommands.items()):
                if not path and name in EXCLUDED_GROUPS:
                    continue
                walk(sub, path + [name])
            return
        if "--json" not in [opt for param in command.params for opt in getattr(param, "opts", [])]:
            return
        argv = list(path)
        for param in command.params:
            if not param.required:
                continue
            if param.param_type_name == "argument":
                argv.append("placeholder_id")
            else:
                argv.extend([param.opts[0], "1.0"])
        found.append((" ".join(path), argv + ["--json"]))

    walk(typer.main.get_command(app), [])
    return found


COMMANDS = _json_commands()

_DRIVER = r"""
import json, sys
from typer.testing import CliRunner
from mdf.cli.main import app
from mdf.core.backend_client import BackendClient

BackendClient._MAX_RETRIES = 0  # a closed port fails at once; no backoff sleeps
runner = CliRunner()
results = {}
for name, argv in json.loads(sys.argv[1]):
    r = runner.invoke(app, argv)  # stdin: empty, not a TTY
    crashed = r.exception is not None and not isinstance(r.exception, SystemExit)
    results[name] = {
        "exit": r.exit_code,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "exception": repr(r.exception) if crashed else None,
    }
with open(sys.argv[2], "w") as fh:
    json.dump(results, fh)
"""


@pytest.fixture(scope="module")
def logged_out_runs(tmp_path_factory) -> Dict[str, dict]:
    home = tmp_path_factory.mktemp("home")
    work = tmp_path_factory.mktemp("work")
    out = tmp_path_factory.mktemp("out") / "results.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith("MDF_")}
    env.update(
        HOME=str(home),
        MDF_API_URL="http://127.0.0.1:9",
        NO_COLOR="1",
        COLUMNS="100",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    subprocess.run(
        [sys.executable, "-c", _DRIVER, json.dumps(COMMANDS), str(out)],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=300,
        check=True,
    )
    return json.loads(out.read_text())


def test_the_tree_has_json_commands():
    names = {name for name, _ in COMMANDS}
    # Guard the walker itself: these must always be covered.
    assert {"list", "show", "search", "status", "publish", "check", "cite", "whoami", "doctor", "admin stats"} <= names


@pytest.mark.parametrize("name", [name for name, _ in COMMANDS])
def test_json_command_logged_out(name, logged_out_runs):
    run = logged_out_runs[name]
    assert run["exception"] is None, f"{name} crashed: {run['exception']}\n{run['stderr']}"
    assert run["exit"] in ALLOWED_EXIT_CODES, f"{name} exited {run['exit']}:\n{run['stdout']}{run['stderr']}"
    try:
        payload = json.loads(run["stdout"])
    except json.JSONDecodeError as exc:  # pragma: no cover - the failure message is the point
        pytest.fail(f"{name}: stdout is not one JSON document ({exc}):\n{run['stdout']!r}")
    assert isinstance(payload, (dict, list))
    if run["exit"] != 0 and isinstance(payload, dict):
        assert payload.get("success") is False and isinstance(payload.get("error"), dict)
    for marker in PROMPT_MARKERS:
        assert marker not in run["stdout"] + run["stderr"], f"{name} tried to prompt"


@pytest.mark.parametrize("name", ["list", "admin stats", "admin pending", "whoami", "clone"])
def test_auth_gated_commands_exit_4_with_login_hint(name, logged_out_runs):
    run = logged_out_runs[name]
    payload = json.loads(run["stdout"])
    assert run["exit"] == 4
    assert payload["error"]["kind"] == "auth"
    assert "mdf login" in payload["error"]["hint"]
