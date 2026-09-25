"""`mdf login`, `mdf logout`, `mdf whoami`."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import typer
from rich.markup import escape

from mdf.cli import theme
from mdf.cli.app import PANEL_AUTH, app
from mdf.cli.formatting import (
    EXIT_AUTH,
    EXIT_USAGE,
    console,
    emit_json,
    is_success,
    json_fail,
    status_code_of,
)
from mdf.cli.options import (
    ApiUrlOpt,
    DevUserOpt,
    JsonOpt,
    ServiceOpt,
    TokenOpt,
    open_client,
)
from mdf.core.backend_client import BackendClient, _api_url_for_service
from mdf.core.config import resolve_service
from mdf.core.exceptions import AuthRequired


def _fetch_identity(
    *,
    service: str,
    api_url: Optional[str] = None,
    token: Optional[str] = None,
    dev_user: Optional[str] = None,
) -> Dict[str, str]:
    """Best-effort ``{"name", "email"}`` for the current credentials.

    Asks the backend (``GET /auth/check``), which reads the identity from the
    token. Never prompts and never fails the caller: any problem (no login,
    network, an old backend) just means no identity is shown — except a 401/403,
    reported as ``{"rejected": "true"}`` so the caller can say the credential is
    invalid.
    """
    try:
        client = BackendClient.authenticated(
            base_url=api_url,
            token=token,
            service_instance=service,
            dev_user_id=dev_user,
            interactive=False,
        )
    except Exception:
        return {}
    try:
        result = client.auth_check()
    except Exception:
        return {}
    finally:
        client.close()
    if not is_success(result):
        # The server looked at the credential and said no: that is not "unknown".
        return {"rejected": "true"} if status_code_of(result) in (401, 403) else {}
    return {key: str(result[key]) for key in ("name", "email") if result.get(key)}



def _identity_label(identity: Dict[str, str]) -> Optional[str]:
    """``Jane Doe <jane@example.org>`` / ``Jane Doe`` / ``jane@example.org``."""
    name, email = identity.get("name"), identity.get("email")
    if name and email:
        return f"{name} <{email}>"
    return name or email or None



def _render_identity(
    *,
    service: str,
    status_str: str,
    token_store: str,
    method: Optional[str] = None,
    env_token: bool = False,
    env_client: bool = False,
    identity: Optional[Dict[str, str]] = None,
) -> None:
    """The identity panel used by `mdf whoami`."""
    authenticated = status_str == "authenticated"
    detail = theme.detail_table(label_width=12)
    who = _identity_label(identity or {})
    if who:
        detail.add_row("Signed in as", escape(who))
    detail.add_row("Service", theme.ident(service))
    detail.add_row(
        "Status",
        theme.mark("ok" if authenticated else "idle", escape(status_str), style="ok" if authenticated else "secondary"),
    )
    if method:
        detail.add_row("Method", escape(str(method)))
    detail.add_row("Token store", escape(str(token_store)))

    notes: List[str] = []
    if env_token:
        notes.append("MDF_CONNECT_TOKEN is set in environment")
    if env_client:
        notes.append("MDF_CLIENT_ID + MDF_CLIENT_SECRET are set in environment")
    if not authenticated:
        notes.append(f"hint: run `mdf login --service {service}`")

    body: Any = detail
    if notes:
        from rich.console import Group as _Group

        body = _Group(detail, "\n" + "\n".join(f"[hint]{escape(note)}[/hint]" for note in notes))

    console.print()
    console.print(theme.panel(
        body,
        title=f"{theme.OK if authenticated else theme.IDLE} Identity",
        subtitle=service,
        title_style="ok" if authenticated else "secondary",
        border="ok" if authenticated else "dim",
    ))



@app.command(rich_help_panel=PANEL_AUTH)
def login(
    service: ServiceOpt = None,
    token: TokenOpt = None,
    save: bool = typer.Option(
        False,
        "--save",
        help="With --token: keep the token for later commands (owner-only file under ~/.config/mdf)",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Never auto-open a browser; print the URL and wait for the pasted code",
    ),
):
    """Authenticate with Globus.

    The login URL is always printed first, then a browser is opened only if this
    looks like a desktop session. Use --no-browser (or set MDF_NO_BROWSER=1) on
    headless hosts where a text browser would take over the terminal.

    For scripts and CI, you can skip interactive login entirely:

      MDF_CLIENT_ID + MDF_CLIENT_SECRET   Globus confidential client credentials
      MDF_CONNECT_TOKEN                   Pre-existing access token
      mdf login --token T --save          Validate T once and keep it for later commands
    """
    from mdf.auth.globus import (
        DATA_MDF_SCOPE,
        DEFAULT_TOKEN_PATH,
        GROUPS_SCOPE,
        TRANSFER_SCOPE,
        get_authorizer_for_scopes,
        get_scopes_for_service,
    )

    resolved = resolve_service(service)

    def _reject_token(detail: str) -> None:
        console.print()
        console.print(theme.error_panel(
            f"Token validation failed for {escape(resolved)}",
            title="Login failed",
            detail=escape(str(detail)),
            hint="check the token, or run `mdf login` for an interactive Globus login",
        ))
        raise typer.Exit(code=1)

    if save and not token:
        console.print()
        console.print(theme.error_panel(
            "--save needs --token",
            title="Nothing to save",
            hint="mdf login --token <ACCESS_TOKEN> --save  (or plain `mdf login` for a Globus login)",
        ))
        raise typer.Exit(code=EXIT_USAGE)

    identity: Dict[str, str] = {}
    saved_path = None
    if token:
        with open_client(public=False, token=token, service=resolved) as client:
            try:
                auth_result = client.auth_check()
            except Exception as exc:
                _reject_token(str(exc))
        if (
            not auth_result
            or auth_result.get("success") is False
            or auth_result.get("authenticated") is False
            or auth_result.get("error")
            or auth_result.get("detail")
        ):
            error = auth_result.get("error") or auth_result.get("detail") or "token was rejected"
            _reject_token(str(error))
        identity = {key: str(auth_result[key]) for key in ("name", "email") if auth_result.get(key)}
        if save:
            from mdf.core.config import save_token

            # Keyed by the API it was validated against: never sent elsewhere.
            saved_path = save_token(token, _api_url_for_service(resolved))
    else:
        scope, _rs = get_scopes_for_service(resolved)
        try:
            # Same policy as every other command (set by MDFGroup): prompt only
            # when stdin and stdout are terminals. Prompts go to stderr.
            get_authorizer_for_scopes(
                [scope, DATA_MDF_SCOPE, TRANSFER_SCOPE, GROUPS_SCOPE],
                no_browser=no_browser,
            )
        except AuthRequired:
            console.print()
            console.print(theme.error_panel(
                "Interactive login needs a terminal (stdin and stdout must be TTYs).",
                title="Login required",
                hint=(
                    "run `mdf login` in a terminal; for scripts/CI set MDF_CONNECT_TOKEN, "
                    "or MDF_CLIENT_ID + MDF_CLIENT_SECRET, or use `mdf login --token T --save`"
                ),
            ))
            raise typer.Exit(code=EXIT_AUTH)
        identity = _fetch_identity(service=resolved)

    detail = theme.detail_table(label_width=12)
    who = _identity_label(identity)
    if who:
        detail.add_row("Signed in as", escape(who))
    detail.add_row("Service", theme.ident(resolved))
    if token and saved_path:
        detail.add_row("Token", f"saved to {escape(str(saved_path))}")
    elif token:
        detail.add_row("Token", "valid (not saved: pass --save to keep it)")
    else:
        detail.add_row("Token store", escape(str(DEFAULT_TOKEN_PATH)))
    console.print()
    console.print(theme.success_panel(detail, title="Authentication ready"))



@app.command(rich_help_panel=PANEL_AUTH)
def logout():
    """Clear cached credentials."""
    from mdf.auth.globus import DEFAULT_TOKEN_PATH
    from mdf.auth.globus import logout as clear_cached_tokens
    from mdf.core.config import clear_saved_token, saved_token_path

    removed_paths = []
    if clear_cached_tokens():
        removed_paths.append(DEFAULT_TOKEN_PATH)
    if clear_saved_token():
        removed_paths.append(saved_token_path())
    if removed_paths:
        console.print(theme.mark("ok", "Logged out", style="ok"))
        removed = ", ".join(escape(str(path)) for path in removed_paths)
        console.print(f"[hint]removed {removed}[/hint]")
    else:
        console.print(theme.check_line(
            "idle",
            "No cached token file found",
            detail=escape(str(DEFAULT_TOKEN_PATH)),
            hint="you were already logged out — run `mdf login` to authenticate",
            indent=0,
        ))



@app.command(rich_help_panel=PANEL_AUTH)
def whoami(
    json_output: JsonOpt = False,
    service: ServiceOpt = None,
    api_url: ApiUrlOpt = None,
    token: TokenOpt = None,
    dev_user: DevUserOpt = None,
):
    """Show who you are, on which service, and how you are authenticated."""
    resolved = resolve_service(service)

    from mdf.auth.globus import DEFAULT_TOKEN_PATH
    from mdf.core.backend_client import CREDENTIAL_LABELS, credential_source

    env_token = bool(os.environ.get("MDF_CONNECT_TOKEN"))
    env_client = bool(
        os.environ.get("MDF_CLIENT_ID") and os.environ.get("MDF_CLIENT_SECRET")
    )
    # The same resolution BackendClient.authenticated uses, so the method
    # reported here is the credential requests actually carry.
    source = credential_source(
        api_url or _api_url_for_service(resolved),
        token=token,
        service_instance=resolved,
        dev_user_id=dev_user,
    )
    method = CREDENTIAL_LABELS.get(source) if source else None

    authenticated = bool(method)
    status_str = "authenticated" if authenticated else "not authenticated"
    identity = (
        _fetch_identity(service=resolved, api_url=api_url, token=token, dev_user=dev_user)
        if authenticated
        else {}
    )
    if identity.pop("rejected", None):
        # A credential exists but the service refuses it (e.g. an expired
        # saved token): report it as invalid, not as authenticated.
        authenticated = False
        status_str = "credentials rejected"

    # `success` is the answer to "am I authenticated?", not "did this
    # command run" — an unauthenticated caller must be able to branch on it
    # (and on the exit code) without parsing the status string.
    result = {
        "success": authenticated,
        "service": resolved,
        "status": status_str,
        "method": method,
        "name": identity.get("name"),
        "email": identity.get("email"),
        "token_store": str(DEFAULT_TOKEN_PATH),
        "env_token_set": env_token,
        "env_client_set": env_client,
    }

    if json_output:
        if not authenticated:
            json_fail(
                kind="auth",
                message="Credentials rejected" if method else "Not authenticated",
                extra={k: v for k, v in result.items() if k != "success"},
            )
        emit_json(result)
        return

    _render_identity(
        service=resolved,
        status_str=status_str,
        token_store=str(DEFAULT_TOKEN_PATH),
        method=method,
        env_token=env_token,
        env_client=env_client,
        identity=identity,
    )
    if not authenticated:
        raise typer.Exit(code=EXIT_AUTH)
