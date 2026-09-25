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
from mdf.core.errors import (  # noqa: F401  (re-exported: the CLI's historical import path)
    EXIT_AUTH,
    EXIT_CODE_BY_KIND,
    EXIT_ERROR,
    EXIT_NETWORK,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE,
    _ERROR_KINDS,
    classify_api_error,
    error_envelope,
    exit_code_for,
    is_success,
    status_code_of,
)
# ``console`` (stdout, data) and ``err_console`` (stderr, chrome) are re-exported
# here because most command modules already import their console from this module.
from mdf.cli.theme import console, err_console  # noqa: F401


def format_status_badge(status: str) -> str:
    """Return a glyph + word badge for a submission status.

    ``✓ published``, ``● pending_curation``, ``✗ rejected``, ``○ withdrawn`` —
    the state word is always preserved so output stays greppable.
    """
    return theme.state_badge(status)


def author_name(author: Any) -> str:
    """One author as display text.

    The API has served authors as bare strings and as objects
    (``{"name", "given_name", "family_name", "affiliations", ...}``), and
    manifests hold :class:`mdf.models.config.Author` models; all three render
    the same way. An object without ``name`` falls back to "Family, Given".
    """
    if author is None:
        return ""
    if isinstance(author, str):
        return author.strip()
    if not isinstance(author, dict):
        if hasattr(author, "model_dump"):
            author = author.model_dump()
        else:
            return str(author).strip()
    name = str(author.get("name") or "").strip()
    if name:
        return name
    given = str(author.get("given_name") or "").strip()
    family = str(author.get("family_name") or "").strip()
    if family and given:
        return f"{family}, {given}"
    return family or given


def format_authors(authors: Any, sep: str = "; ") -> str:
    """Join a list of authors (strings, dicts or Author models) for display.

    The default separator is ``"; "`` because names arrive as "Family, Given",
    so a comma would be ambiguous. Blank entries are dropped.
    """
    if authors is None:
        return ""
    if isinstance(authors, (str, dict)) or not isinstance(authors, (list, tuple)):
        authors = [authors]
    return sep.join(name for name in (author_name(a) for a in authors) if name)


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

    from mdf.core import config as core_config
    from mdf.core.backend_client import BackendClient, _api_url_for_service
    from mdf.core.exceptions import AuthRequired

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
        or core_config.load_saved_token(base)
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
        try:
            return BackendClient.authenticated(
                base_url=base, token=token, service_instance=service, dev_user_id=dev_user
            )
        except AuthRequired:
            # e.g. a cached login that must not go to an untrusted --api-url:
            # public reads still work anonymously.
            pass
    return BackendClient(base_url=base)


@contextmanager
def api_spinner(message: str = "Loading..."):
    """Show a ``dots`` spinner on stderr while waiting for an API call.

    Auto-suppresses in pipes/non-TTY, so machine output is never polluted.
    """
    with theme.spinner(message):
        yield


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


def interactive_stdin() -> bool:
    """True when there is a human on the other end who can answer a prompt."""
    import sys

    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def login_allowed(json_mode: bool) -> bool:
    """The CLI's one rule for starting an interactive Globus login.

    Only when a human is at both ends — stdin and stdout are TTYs — and the
    caller did not ask for ``--json``. Everywhere else a missing login is an
    ``auth`` error (exit 4), never a prompt.
    """
    import sys

    if json_mode:
        return False
    try:
        return interactive_stdin() and sys.stdout.isatty()
    except Exception:
        return False


def fail_auth_required(exc: BaseException, *, json_mode: bool) -> None:
    """Report a login that was needed but not allowed; exit :data:`EXIT_AUTH`.

    Never returns. ``--json`` gets the shared envelope (kind ``auth``);
    human mode gets the error panel.
    """
    import typer
    from rich.markup import escape

    message, hint, _border = _ERROR_KINDS["auth"]
    hint = getattr(exc, "hint", None) or hint
    detail = str(exc) if str(exc) and str(exc) != message else None
    if json_mode:
        envelope = error_envelope(kind="auth", message=message, hint=hint)
        if detail:
            envelope["error"]["detail"] = detail
        emit_json(envelope)
        raise typer.Exit(code=EXIT_AUTH)
    console.print()
    console.print(
        theme.error_panel(
            message,
            title="Login required",
            detail=escape(detail) if detail else None,
            hint=hint,
        )
    )
    raise typer.Exit(code=EXIT_AUTH)


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
