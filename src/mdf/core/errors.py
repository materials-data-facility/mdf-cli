"""The one error vocabulary shared by the CLI and the MCP server.

Pure functions, no terminal output: classify an API result into a ``kind``,
map kinds to exit codes, and build the ``{"success": false, "error": {...}}``
envelope. :mod:`mdf.cli.formatting` renders these for humans; the MCP server
returns the envelope to agents.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


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
        "fix the highlighted fields, then re-run `mdf check` to verify",
        "red",
    ),
    "server": (
        "Server error",
        "the backend may be busy or deploying — retry, then `mdf doctor`",
        "red",
    ),
    # No HTTP status ever arrived: the request never reached the API.
    "network": (
        None,
        "check connectivity with `mdf doctor`, or select another --service",
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
