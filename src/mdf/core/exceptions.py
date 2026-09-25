"""Custom exceptions for MDF CLI."""

from __future__ import annotations


class MDFError(Exception):
    """Base exception for MDF CLI errors."""

    pass


class NoManifestError(MDFError):
    """Raised when a command requires an mdf.yaml manifest but none exists."""

    def __init__(self, path: str = "."):
        self.path = path
        super().__init__(f"No mdf.yaml found in {path}")


class AuthRequired(MDFError):
    """Raised when a Globus login is needed but prompting is not allowed.

    Library code never prompts on its own initiative: callers that cannot host
    an interactive login (a ``--json`` run, a pipe, an agent, CI) pass
    ``interactive=False`` — or run inside
    :func:`mdf.auth.globus.interactive_login` ``(False)`` — and get this error
    instead of a prompt. Recover with ``mdf login`` or by setting
    ``MDF_CONNECT_TOKEN`` / ``MDF_CLIENT_ID`` + ``MDF_CLIENT_SECRET``.
    """

    def __init__(self, message: str = "Authentication required", *, hint: str | None = None):
        super().__init__(message)
        #: Recovery advice more specific than "run `mdf login`", when there is one.
        self.hint = hint
