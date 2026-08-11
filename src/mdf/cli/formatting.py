"""Shared output formatting for MDF CLI commands.

Provides consistent Rich-formatted output for API results, status badges,
and error interpretation across all CLI commands. The visual vocabulary itself
(colors, glyphs, panels, tables, progress) lives in :mod:`mdf.cli.theme`; this
module is the API-result layer on top of it.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

from mdf.cli import theme
# ``console`` (stdout, data) and ``err_console`` (stderr, chrome) are re-exported
# here because most command modules already import their console from this module.
from mdf.cli.theme import console, err_console  # noqa: F401


def format_status_badge(status: str) -> str:
    """Return a glyph + word badge for a submission status.

    ``✓ published``, ``● pending_curation``, ``✗ rejected``, ``○ withdrawn`` —
    the state word is always preserved so output stays greppable.
    """
    return theme.state_badge(status)


def read_client(
    api_url: Optional[str] = None,
    token: Optional[str] = None,
    service: Optional[str] = None,
    dev_user: Optional[str] = None,
):
    """Build a BackendClient for PUBLIC read endpoints.

    Public GETs (search, card, citation, related, preview, health) are served
    unauthenticated, so — unlike ``BackendClient.authenticated`` — this never
    triggers an interactive browser login. It still uses explicit credentials
    when they are available (so private/pending records remain reachable), but a
    missing login does not block reading public data.

    Also turns an unknown ``--service`` into a clean error instead of a raw
    traceback.
    """
    import typer

    from mdf.core.backend_client import BackendClient, _api_url_for_service

    try:
        base = api_url or _api_url_for_service(service)
    except ValueError as exc:
        from rich.markup import escape

        console.print(
            theme.error_panel(
                escape(str(exc)),
                title="Unknown service",
                hint="pick one of staging, prod, dev, local — or pass --api-url",
            )
        )
        raise typer.Exit(code=1)

    has_creds = bool(
        token
        or os.environ.get("MDF_CONNECT_TOKEN")
        or (os.environ.get("MDF_CLIENT_ID") and os.environ.get("MDF_CLIENT_SECRET"))
        or dev_user
        or os.environ.get("MDF_DEV_USER_ID")
    )
    # A cached interactive login also counts: route logged-in users through the
    # authenticated client (uses the cached token, no new browser prompt) so they
    # can read ACL-restricted datasets they own and reach auth-gated endpoints
    # (e.g. /search/semantic). Anonymous users still get a public-only client and
    # are never forced into a login prompt.
    if not has_creds:
        try:
            from mdf.auth.globus import is_logged_in
            has_creds = is_logged_in(service_instance=service)
        except Exception:
            has_creds = False
    if has_creds:
        return BackendClient.authenticated(
            base_url=base, token=token, service_instance=service, dev_user_id=dev_user
        )
    return BackendClient(base_url=base)


@contextmanager
def api_spinner(message: str = "Loading..."):
    """Show a ``dots`` spinner on stderr while waiting for an API call.

    Auto-suppresses in pipes/non-TTY, so machine output is never polluted.
    """
    with theme.spinner(message):
        yield


def json_or_rich(result: dict, json_mode: bool, render_fn: Callable) -> None:
    """If json_mode, dump raw JSON to stdout. Otherwise call render_fn."""
    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        render_fn(result)


def status_code_of(result: Any) -> Optional[int]:
    """Return the HTTP status a result arrived with, or None if unknown.

    ``BackendClient._request`` returns an ``ApiResult`` (a dict subclass) that
    carries ``status_code``. A plain dict with a ``_status_code`` key is also
    accepted so hand-built payloads and test fixtures can express a status.
    """
    code = getattr(result, "status_code", None)
    if code is None and isinstance(result, dict):
        code = result.get("_status_code")
    if code is None:
        return None
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def is_success(result: Any) -> bool:
    """The single definition of "this API call worked".

    When the HTTP status is known it decides: 2xx is success, everything else is
    not. A 2xx that still carries an explicit ``"success": False`` envelope is
    treated as a failure, since the server said so.

    When the status is unknown (client-synthesized results, older call paths,
    hand-built dicts) fall back to the envelope heuristic: an explicit
    ``success`` key wins; otherwise a payload with no error marker is success —
    the live API does not consistently wrap responses in an envelope.
    """
    code = status_code_of(result)
    if not isinstance(result, dict):
        return code is not None and 200 <= code < 300
    if code is not None:
        if 200 <= code < 300:
            return bool(result.get("success", True))
        return False
    if "success" in result:
        return bool(result["success"])
    return not (result.get("error") or result.get("detail"))


#: kind -> (message, hint, border). ``kind`` is what classification decides;
#: the message wording is load-bearing (scripts and docs grep for it).
_ERROR_KINDS: Dict[str, tuple] = {
    "auth": (
        "Authentication required",
        "run `mdf login` (or set MDF_CONNECT_TOKEN for CI)",
        "red",
    ),
    "forbidden": (
        "Permission denied",
        "you may not own this record — check --service, or ask a curator",
        "red",
    ),
    "rate_limit": (
        "Rate limited — try again shortly",
        "wait a few seconds, then retry the same command",
        "yellow",
    ),
    "not_found": (
        "Not found",
        "check the identifier, or find it with `mdf search <query>`",
        "yellow",
    ),
    "validation": (
        "Validation error",
        "fix the highlighted fields, then re-run with --preflight-only to verify",
        "red",
    ),
    "server": (
        "Server error",
        "the backend may be busy or deploying — retry, then `mdf config doctor`",
        "red",
    ),
    # No HTTP status ever arrived: the request never reached the API.
    "network": (
        None,
        "check connectivity with `mdf config doctor`, or select another --service",
        "red",
    ),
    "unknown": (
        None,
        "re-run with --json to see the raw response",
        "red",
    ),
}

_NETWORK_MARKERS = (
    "connection failed",
    "cannot connect",
    "connect error",
    "connecterror",
    "connection refused",
    "timed out",
    "timeout",
    "name or service not known",
    "nodename nor servname",
)


def classify_api_error(result: Any) -> Dict[str, Optional[str]]:
    """Classify a failed API result into the CLI's error vocabulary.

    Prefers the propagated HTTP status code and only falls back to
    substring-matching the error text when no status is available. Returns
    ``{"kind", "code", "message", "detail", "hint", "border"}`` — ``detail`` is
    the raw server text (unescaped).
    """
    status = status_code_of(result)
    raw_error = "Unknown error"
    if isinstance(result, dict):
        raw_error = result.get("error") or result.get("detail") or "Unknown error"

    kind = "unknown"
    if status is not None:
        if status in (401, 407):
            kind = "auth"
        elif status == 403:
            kind = "forbidden"
        elif status == 429:
            kind = "rate_limit"
        elif status == 404:
            kind = "not_found"
        elif status in (400, 422):
            kind = "validation"
        elif status >= 500:
            kind = "server"
    else:
        error_str = str(raw_error).lower()
        if "401" in error_str or "unauthorized" in error_str or "not authenticated" in error_str:
            kind = "auth"
        elif "403" in error_str or "forbidden" in error_str:
            kind = "forbidden"
        elif "429" in error_str or "rate limit" in error_str:
            kind = "rate_limit"
        elif "404" in error_str or "not found" in error_str:
            kind = "not_found"
        elif "validation" in error_str:
            kind = "validation"
        elif any(marker in error_str for marker in _NETWORK_MARKERS):
            kind = "network"

    message, hint, border = _ERROR_KINDS[kind]
    if kind == "server" and status is not None:
        message = f"Server error (HTTP {status})"
    if message is None:
        message = str(raw_error)

    # Only show the raw server text when it adds something beyond the message.
    detail: Optional[str] = str(raw_error)
    if detail == message or kind in ("auth", "forbidden", "rate_limit", "not_found"):
        detail = None

    code = f"HTTP {status}" if status is not None else None
    return {
        "kind": kind,
        "code": code,
        "message": message,
        "detail": detail,
        "hint": hint,
        "border": border,
    }


def handle_api_result(
    result: Dict[str, Any],
    success_msg: Optional[str] = None,
    error_prefix: str = "Error",
) -> bool:
    """Interpret an API result and print formatted output.

    Success prints a ``✓`` line; failure prints the contract's ``✗``-titled panel
    (error code in the title, message in the body, recovery ``hint:`` last).

    Returns True on success, False on error.
    """
    if is_success(result):
        if success_msg:
            console.print(theme.mark("ok", success_msg, style="ok"))
        return True

    from rich.markup import escape

    info = classify_api_error(result)
    console.print(
        theme.error_panel(
            escape(str(info["message"])),
            title=error_prefix,
            code=info["code"],
            detail=escape(str(info["detail"])) if info["detail"] else None,
            hint=info["hint"],
            border=info["border"] or "red",
        )
    )
    return False


def require_success(result: dict, error_prefix: str = "Error") -> None:
    """Check API result and raise typer.Exit(1) on failure."""
    import typer

    if not handle_api_result(result, error_prefix=error_prefix):
        raise typer.Exit(code=1)


def format_result_or_json(
    result: Dict[str, Any],
    json_mode: bool,
    success_msg: Optional[str] = None,
    error_prefix: str = "Error",
) -> bool:
    """If json_mode, dump raw JSON. Otherwise use handle_api_result."""
    if json_mode:
        print(json.dumps(result, indent=2))
        return is_success(result)
    return handle_api_result(result, success_msg=success_msg, error_prefix=error_prefix)
