"""`mdf config show|get|set|path` and `mdf doctor`."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import typer
from rich.markup import escape

from mdf.core.config import GlobalConfig, resolve_service
from mdf.cli import theme
from mdf.cli.options import ApiUrlOpt, JsonOpt, ServiceOpt
from mdf.cli.theme import console

app = typer.Typer(help="Show and change CLI defaults (~/.config/mdf/config.json)", no_args_is_help=True)


@app.command("show")
def show():
    """Show current configuration."""
    cfg = GlobalConfig()
    if cfg.data:
        console.print(theme.json_syntax(cfg.data))
    else:
        console.print(theme.empty_panel(
            "No configuration set yet.",
            title="no config",
            hints=[
                "set a default with `mdf config set user.organization \"My Lab\"`",
                f"config file would live at {cfg.path}",
            ],
        ))


@app.command("set")
def set_value(
    key: str = typer.Argument(..., help="Dotted key (e.g. defaults.service)"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a configuration value."""
    from mdf.core.config import validate_config_value

    warning = validate_config_value(key, value)
    if warning:
        console.print(theme.mark("active", f"Warning: {escape(str(warning))}", style="warn"))

    cfg = GlobalConfig()
    cfg.set(key, value)
    console.print(
        theme.mark("ok", f"Set {escape(key)} = {theme.number(value)}", style=None)
    )


@app.command("get")
def get_value(
    key: str = typer.Argument(..., help="Dotted key (e.g. defaults.service)"),
):
    """Get a configuration value."""
    cfg = GlobalConfig()
    val = cfg.get(key)
    if val is None:
        console.print(f"[secondary]{escape(key)} is not set[/secondary]")
        raise typer.Exit(code=1)
    if isinstance(val, (dict, list)):
        console.print(json.dumps(val, indent=2))
    else:
        console.print(str(val))


@app.command("path")
def path():
    """Show configuration file path."""
    cfg = GlobalConfig()
    console.print(str(cfg.path))


def doctor(
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
):
    """Check your setup: service, connectivity, login, and the local mdf.yaml.

    Exits 0 when everything checks out; otherwise with the code of the first
    problem (5 service/connectivity, 4 login, 2 manifest).

    Examples:
        mdf doctor
        mdf doctor --json
    """
    from mdf.version import __version__
    from mdf.core.agent import MDFAgent
    from mdf.core.backend_client import BackendClient, _api_url_for_service
    from mdf.cli.formatting import api_spinner
    from mdf.cli.preflight import auth_ready, run_preflight

    resolved = resolve_service(service)
    checks: list[dict] = []

    cfg_path = Path.home() / ".config" / "mdf" / "config.json"
    checks.append({
        "name": "Config",
        "ok": True,
        "detail": str(cfg_path) if cfg_path.exists() else f"{cfg_path} (not created yet; optional)",
    })

    try:
        env_tok = bool(os.environ.get("MDF_CONNECT_TOKEN"))
        auth_ok = auth_ready(resolved)
        auth_detail = "Auth ready" if auth_ok else ("MDF_CONNECT_TOKEN set" if env_tok else "Not authenticated")
    except Exception:
        auth_ok = False
        auth_detail = "Error checking auth"
    checks.append({"name": "Auth", "ok": auth_ok, "detail": auth_detail})

    try:
        base = api_url or _api_url_for_service(resolved)
        service_detail = f"{resolved} ({base[:30]}...)" if len(base) > 30 else f"{resolved} ({base})"
        checks.append({"name": "Service", "ok": True, "detail": service_detail})
    except Exception as e:
        checks.append({"name": "Service", "ok": False, "detail": str(e)})
        base = None

    if base:
        try:
            t0 = time.monotonic()
            client = BackendClient(base_url=base)
            with api_spinner("Checking connectivity..."):
                health = client.health()
            client.close()
            ms = round((time.monotonic() - t0) * 1000)
            conn_ok = bool(health.get("success", health.get("status") == "ok"))
            detail = f"Backend healthy ({ms}ms)" if conn_ok else str(health.get("error") or health.get("detail") or "unhealthy")
            checks.append({"name": "Connectivity", "ok": conn_ok, "detail": detail})
        except Exception as e:
            checks.append({"name": "Connectivity", "ok": False, "detail": str(e)})
    else:
        checks.append({"name": "Connectivity", "ok": False, "detail": "No service URL"})

    mdf_yaml = Path.cwd() / "mdf.yaml"
    if mdf_yaml.exists():
        try:
            agent = MDFAgent.from_manifest(".")
            preflight = run_preflight(
                agent.manifest,
                root=agent.root or Path.cwd(),
                service=resolved,
                api_url=base,
                submit=False,
            )
            blocking = sum(1 for issue in preflight["issues"] if issue["severity"] == "blocking")
            warnings = sum(1 for issue in preflight["issues"] if issue["severity"] == "warning")
            checks.append({"name": "Manifest", "ok": blocking == 0, "detail": f"{blocking} blocking, {warnings} warning issue(s)"})
        except Exception as e:
            checks.append({"name": "Manifest", "ok": False, "detail": f"mdf.yaml invalid: {e}"})
    else:
        checks.append({"name": "Manifest", "ok": True, "detail": "No mdf.yaml here (only needed to publish)"})

    failed = [c["name"] for c in checks if not c["ok"]]
    kind = None
    if failed:
        if "Service" in failed or "Connectivity" in failed:
            kind = "network"
        elif "Auth" in failed:
            kind = "auth"
        else:
            kind = "validation"

    if json_output:
        from mdf.cli.formatting import json_fail, json_ok

        if kind:
            json_fail(kind=kind, message=f"{len(failed)} check(s) failed: {', '.join(failed)}", extra={"checks": checks})
        json_ok({"checks": checks})
        return

    console.print()
    console.print(theme.rule(f"{theme.SCIENCE} mdf doctor  v{__version__}"))
    console.print()
    checklist = theme.checklist_table()
    for c in checks:
        theme.add_check(
            checklist,
            "ok" if c["ok"] else "err",
            escape(str(c["name"])),
            detail=escape(str(c["detail"])),
        )
    console.print(checklist)
    console.print()
    if not kind:
        console.print(theme.mark("ok", "Everything checks out.", style="ok"))
        console.print()
        return
    console.print(
        "[hint]hint: fix the ✗ rows above — `mdf login` for auth, `mdf init` for a manifest, "
        "--service to pick another service[/hint]"
    )
    console.print()
    from mdf.cli.formatting import EXIT_CODE_BY_KIND

    raise typer.Exit(code=EXIT_CODE_BY_KIND[kind])
