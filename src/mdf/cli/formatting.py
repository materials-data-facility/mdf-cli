"""Shared output formatting for MDF CLI commands.

Provides consistent Rich-formatted output for API results, status badges,
and error interpretation across all CLI commands. The visual vocabulary itself
(colors, glyphs, panels, tables, progress) lives in :mod:`mdf.cli.theme`; this
module is the API-result layer on top of it.

``--json`` output contract
--------------------------
Every command that takes ``--json`` obeys one contract, implemented once here
and applied at each call site:

* **stdout is byte-plain JSON.** Nothing else — no Rich markup, no panels, no
  spinners, no "Resolved DOI to…" notices. All chrome goes to stderr (see
  :func:`chrome`), so ``mdf … --json | jq`` always parses.
* **Success** keeps the payload shape each command already emitted, with
  ``"success": true`` added when it was missing (:func:`json_ok`).
* **Failure** emits exactly one envelope (:func:`error_envelope`)::

      {"success": false,
       "error": {"kind": ..., "code": <http status or null>,
                 "message": ..., "hint": ...}}

  ``error.detail`` carries the raw server text when it says more than
  ``message``, and any other key the failed payload carried (``source_id``,
  ``preflight``, …) is passed through at the top level, so nothing is lost.
* **Exit codes** are derived from the error ``kind`` by :func:`exit_code_for`:
  ``0`` ok, ``1`` generic, ``2`` usage, ``3`` not found, ``4`` auth,
  ``5`` network/service. A ``"success": false`` payload is never accompanied by
  exit status 0.
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
    json_mode: bool = False,
):
    """Build a BackendClient for PUBLIC read endpoints.

    Public GETs (search, card, citation, related, preview, health) are served
    unauthenticated, so — unlike ``BackendClient.authenticated`` — this never
    triggers an interactive browser login. It still uses explicit credentials
    when they are available (so private/pending records remain reachable), but a
    missing login does not block reading public data.

    Also turns an unknown ``--service`` into a clean error instead of a raw
    traceback. With ``json_mode`` that error is the shared JSON envelope on
    stdout (kind ``validation`` → exit 2) instead of a Rich panel.
    """
    import typer

    from mdf.core.backend_client import BackendClient, _api_url_for_service

    _hint = "pick one of staging, prod, dev, local — or pass --api-url"
    try:
        base = api_url or _api_url_for_service(service)
    except ValueError as exc:
        if json_mode:
            json_fail(kind="validation", message=str(exc), hint=_hint)
        from rich.markup import escape

        console.print(
            theme.error_panel(
                escape(str(exc)),
                title="Unknown service",
                hint=_hint,
            )
        )
        raise typer.Exit(code=EXIT_USAGE)

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


def _kind_from_error_text(raw_error: Any) -> str:
    """Best-effort classification from the server's error text alone."""
    error_str = str(raw_error).lower()
    if "401" in error_str or "unauthorized" in error_str or "not authenticated" in error_str:
        return "auth"
    if "403" in error_str or "forbidden" in error_str:
        return "forbidden"
    if "429" in error_str or "rate limit" in error_str:
        return "rate_limit"
    if "404" in error_str or "not found" in error_str or "no versions found" in error_str:
        return "not_found"
    if "validation" in error_str:
        return "validation"
    if any(marker in error_str for marker in _NETWORK_MARKERS):
        return "network"
    return "unknown"


def classify_api_error(result: Any) -> Dict[str, Optional[str]]:
    """Classify a failed API result into the CLI's error vocabulary.

    Prefers the propagated HTTP status code and only falls back to
    substring-matching the error text when no status is available. Returns
    ``{"kind", "code", "message", "detail", "hint", "border"}`` — ``detail`` is
    the raw server text (unescaped).
    """
    status = status_code_of(result)
    if status is not None and 200 <= status < 300:
        # A 2xx carrying {"success": false} (e.g. GET /status on a hidden or
        # missing record) says nothing about the failure kind; classify from
        # the text and do not report a misleading "code": 200.
        status = None
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
    if kind == "unknown":
        # Either no status arrived at all, or one arrived that carries no
        # verdict of its own — some endpoints answer 200 with
        # ``{"success": false, "error": "..."}``. Read the text in both cases;
        # a status that DOES map (403 vs "record not found") still wins.
        kind = _kind_from_error_text(raw_error)

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
    """Check API result and exit with the mapped code on failure.

    Human mode and ``--json`` share one exit-code contract (see
    :data:`EXIT_CODE_BY_KIND`), so scripts that do not parse JSON can still
    branch on ``$?``.
    """
    import typer

    if not handle_api_result(result, error_prefix=error_prefix):
        raise typer.Exit(code=exit_code_for(result))


def format_result_or_json(
    result: Dict[str, Any],
    json_mode: bool,
    success_msg: Optional[str] = None,
    error_prefix: str = "Error",
) -> bool:
    """If json_mode, emit the JSON contract (envelope on failure). Otherwise use handle_api_result.

    Callers that ignore the return value still exit non-zero on failure: a
    ``--json`` failure must never leave ``$?`` at 0.
    """
    if json_mode:
        ok = is_success(result)
        if ok:
            print(json.dumps(result, indent=2))
        else:
            import typer

            print(json.dumps(error_envelope(result), indent=2))
            raise typer.Exit(code=exit_code_for(result))
        return ok
    return handle_api_result(result, success_msg=success_msg, error_prefix=error_prefix)


# ---------------------------------------------------------------------------
# Exit codes + the one --json failure envelope
# ---------------------------------------------------------------------------

#: The CLI's exit-code contract. Machine callers switch on these, so the
#: mapping is public API: keep it stable and keep it in one place.
EXIT_OK = 0          # success
EXIT_ERROR = 1       # generic / unclassified failure
EXIT_USAGE = 2       # bad flags, bad option values, invalid input
EXIT_NOT_FOUND = 3   # HTTP 404 — no such dataset/submission
EXIT_AUTH = 4        # HTTP 401/403 — login required or permission denied
EXIT_NETWORK = 5     # never reached the API, or 5xx/429 from it

#: ``classify_api_error()["kind"]`` -> exit code. Anything unmapped is
#: :data:`EXIT_ERROR`.
EXIT_CODE_BY_KIND: Dict[str, int] = {
    "validation": EXIT_USAGE,
    "not_found": EXIT_NOT_FOUND,
    "auth": EXIT_AUTH,
    "forbidden": EXIT_AUTH,
    "rate_limit": EXIT_NETWORK,
    "server": EXIT_NETWORK,
    "network": EXIT_NETWORK,
    "unknown": EXIT_ERROR,
}


def exit_code_for(result: Any) -> int:
    """Map an API result onto the CLI's exit-code contract.

    Successful results map to ``0``; failures map through their classified
    ``kind`` (see :data:`EXIT_CODE_BY_KIND`).
    """
    if is_success(result):
        return EXIT_OK
    return EXIT_CODE_BY_KIND.get(classify_api_error(result)["kind"], EXIT_ERROR)


def interactive_stdin() -> bool:
    """True when there is a human on the other end who can answer a prompt."""
    import sys

    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def confirm_or_abort(
    question: str,
    *,
    yes: bool,
    json_mode: bool = False,
    is_interactive: Optional[Callable[[], bool]] = None,
) -> None:
    """Gate a state-changing action behind a confirmation.

    The one implementation for every mutating command (dataset withdraw /
    resubmit, admin approve / reject / delete / rebuild-embeddings).

    ``--yes`` always skips it. Under ``--json`` a prompt is impossible — it
    would write chrome onto stdout and block a pipeline — so the missing
    ``--yes`` is a usage error (the shared envelope, exit
    :data:`EXIT_USAGE`) rather than a silent go-ahead. A non-TTY stdin without
    ``--json`` keeps the historical behaviour of proceeding, so existing
    scripts are unaffected.
    """
    import typer

    if yes:
        return
    if json_mode:
        json_fail(
            kind="validation",
            message="Confirmation required.",
            hint="pass --yes to confirm non-interactively",
        )
    probe = is_interactive or interactive_stdin
    if not probe():
        return
    if not typer.confirm(question, default=False):
        raise typer.Exit(code=EXIT_ERROR)


def chrome(json_mode: bool):
    """Return the console that human chrome may be written to.

    In ``--json`` mode stdout is reserved for the payload, so notices and
    warnings are diverted to stderr; otherwise they stay on stdout with the
    rest of the human output.
    """
    return err_console if json_mode else console


def error_envelope(
    result: Any = None,
    *,
    kind: Optional[str] = None,
    message: Optional[str] = None,
    code: Optional[int] = None,
    hint: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the CLI's single ``--json`` failure envelope.

    Shape::

        {"success": false,
         "error": {"kind": ..., "code": <http status or null>,
                   "message": ..., "hint": ...}}

    ``kind``/``message``/``code``/``hint`` default to what
    :func:`classify_api_error` decided for ``result``; pass them explicitly for
    failures that never involved an API call (bad flag values, missing
    manifest). Every other key of ``result`` — ``detail``, ``source_id``,
    ``preflight``, … — is passed through at the top level so a machine caller
    loses nothing by the rewrap. ``extra`` is merged last.
    """
    info = classify_api_error(result) if kind is None else None
    resolved_kind = kind or (info["kind"] if info else "unknown")
    if message is not None:
        resolved_message: Any = message
    elif info is not None:
        resolved_message = info["message"]
    else:
        resolved_message = resolved_kind
    resolved_hint = hint
    if resolved_hint is None:
        if info is not None:
            resolved_hint = info["hint"]
        elif resolved_kind in _ERROR_KINDS:
            resolved_hint = _ERROR_KINDS[resolved_kind][1]
    if code is not None:
        resolved_code = code
    else:
        status = status_code_of(result)
        # A 2xx that carried {"success": false} says nothing about the failure;
        # report null, never a misleading "code": 200. Other statuses stay ints.
        resolved_code = None if (status is not None and 200 <= status < 300) else status

    error: Dict[str, Any] = {
        "kind": resolved_kind,
        "code": resolved_code,
        "message": str(resolved_message),
        "hint": resolved_hint,
    }
    # ``classify_api_error`` hides the raw server text for kinds whose canned
    # message says it all (that keeps the human panel tidy). A machine caller
    # must not lose it, so keep it as ``error.detail`` whenever it adds
    # anything beyond ``message``.
    if isinstance(result, dict):
        raw = result.get("error") or result.get("detail")
        if raw and str(raw) != error["message"]:
            error["detail"] = str(raw)
    envelope: Dict[str, Any] = {"success": False, "error": error}
    if isinstance(result, dict):
        for key, value in result.items():
            if key in ("success", "error", "_status_code"):
                continue
            envelope[key] = value
    if extra:
        envelope.update(extra)
    return envelope


def emit_json(payload: Any) -> None:
    """Write ``payload`` to stdout as byte-plain JSON — never through Rich."""
    print(json.dumps(payload, indent=2))


def json_ok(payload: Any) -> None:
    """Emit a ``--json`` success payload, adding ``"success": true`` if absent.

    The payload shape is otherwise preserved verbatim, so existing machine
    callers keep working. Non-dict payloads (e.g. ``search --brief``'s list)
    pass through unchanged — there is nowhere to put an envelope flag.
    """
    if isinstance(payload, dict) and "success" not in payload:
        payload = {"success": True, **payload}
    emit_json(payload)


def json_fail(
    result: Any = None,
    *,
    kind: Optional[str] = None,
    message: Optional[str] = None,
    code: Optional[int] = None,
    hint: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    exit_code: Optional[int] = None,
) -> None:
    """Emit the ``--json`` failure envelope on stdout, then exit non-zero.

    Never returns: always raises ``typer.Exit``. The code comes from the
    envelope's ``kind`` unless ``exit_code`` overrides it.
    """
    import typer

    envelope = error_envelope(
        result, kind=kind, message=message, code=code, hint=hint, extra=extra
    )
    emit_json(envelope)
    if exit_code is None:
        exit_code = EXIT_CODE_BY_KIND.get(envelope["error"]["kind"], EXIT_ERROR)
    raise typer.Exit(code=exit_code)


def emit_json_result(result: Any, payload: Any = None, *, extra: Optional[Dict[str, Any]] = None) -> None:
    """The whole ``--json`` contract for one API result, in one call.

    Success emits ``payload`` (default: ``result``) with ``"success": true``.
    Failure emits the shared envelope and raises ``typer.Exit`` with the mapped
    code. Either way stdout carries nothing but JSON.
    """
    if is_success(result):
        json_ok(result if payload is None else payload)
        return
    json_fail(result, extra=extra)


def resolve_identifier_with_notice(client: Any, raw_id: str, *, json_mode: bool = False) -> str:
    """Resolve a DOI to a source_id, announcing the substitution as chrome.

    The single implementation for every command that accepts "source_id or
    DOI". The notice is chrome, so under ``--json`` it goes to stderr and
    stdout stays parseable.
    """
    from mdf.cli.preflight import resolve_dataset_identifier

    resolved_id = resolve_dataset_identifier(raw_id, client)
    if resolved_id != raw_id:
        chrome(json_mode).print(
            f"[secondary]Resolved DOI to source ID:[/secondary] {theme.ident(resolved_id)}"
        )
    return resolved_id
