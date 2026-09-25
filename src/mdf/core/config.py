"""Global configuration for MDF CLI CLI.

Persists user defaults and last-publish state to ~/.config/mdf/config.json.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# flip to 'prod' at cutover (burndown O-1) — requires api.materialsdatafacility.org to exist (O-5)
DEFAULT_SERVICE = "staging"


def _default_config_path() -> Path:
    return Path.home() / ".config" / "mdf" / "config.json"


class GlobalConfig:
    """JSON-backed global config at ~/.config/mdf/config.json."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _default_config_path()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")

    # --- Typed properties ---

    @property
    def service(self) -> Optional[str]:
        return self.get("defaults.service")

    @property
    def last_source_id(self) -> Optional[str]:
        return self.get("last_publish.source_id")

    @property
    def last_version(self) -> Optional[str]:
        return self.get("last_publish.version")

    @property
    def organization(self) -> Optional[str]:
        return self.get("user.organization")

    @property
    def publisher(self) -> Optional[str]:
        return self.get("user.publisher")

    # --- Dotted key access ---

    def get(self, dotted_key: str, default: Any = None) -> Any:
        keys = dotted_key.split(".")
        node = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, dotted_key: str, value: Any) -> None:
        keys = dotted_key.split(".")
        node = self._data
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value
        self._save()

    # --- Convenience ---

    def record_publish(self, source_id: str, version: Optional[str], service: str) -> None:
        self._data.setdefault("last_publish", {})
        self._data["last_publish"].update(
            source_id=source_id,
            version=version,
            service=service,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._save()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def data(self) -> dict:
        return self._data


_VALID_SERVICES = {"prod", "staging", "dev", "local"}


def validate_config_value(key: str, value: str) -> Optional[str]:
    """Validate a config value for known keys.

    Returns a warning message if the value is suspect, or None if valid.
    Unknown keys pass through without validation.
    """
    import re

    if key == "defaults.service":
        if value.lower() not in _VALID_SERVICES:
            return f"Unknown service '{value}'. Expected one of: {', '.join(sorted(_VALID_SERVICES))}"
    elif key == "user.email":
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            return f"'{value}' doesn't look like a valid email address"
    elif key == "globus.local_endpoint_id":
        if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", value.lower()):
            return f"'{value}' doesn't look like a valid UUID (expected format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
    return None


def resolve_service(explicit: Optional[str]) -> str:
    """Resolve the service: explicit > MDF_SERVICE > config > release default."""
    if explicit:
        return explicit
    environment = os.environ.get("MDF_SERVICE")
    if environment:
        return environment
    cfg = GlobalConfig()
    return cfg.service or DEFAULT_SERVICE


# ---------------------------------------------------------------------------
# Saved access token (`mdf login --token T --save`)
# ---------------------------------------------------------------------------


def saved_token_path() -> Path:
    """Where ``mdf login --token --save`` keeps access tokens (mode 0600).

    A JSON object mapping an API base URL to the token saved for it, so a token
    is only ever sent to the service it was validated against. Deliberately
    separate from ``config.json`` (which ``mdf config show`` prints) and from
    the Globus token store (``tokens.json``, owned by globus_sdk).
    """
    return _default_config_path().parent / "access_tokens.json"


def _token_key(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/").lower()


def _read_saved_tokens() -> dict:
    try:
        data = json.loads(saved_token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_saved_tokens(tokens: dict) -> Path:
    """Atomically replace the saved-token file (owner-only), never via a symlink."""
    path = saved_token_path()
    if path.is_symlink():
        raise OSError(f"Refusing to write saved tokens through a symlink: {path}")
    if not tokens:
        if path.exists():
            path.unlink()
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(tokens, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def load_saved_token(base_url: str) -> Optional[str]:
    """The token saved for the API at ``base_url``, or None.

    Never falls back to a token saved for another service.
    """
    token = _read_saved_tokens().get(_token_key(base_url))
    return str(token).strip() or None if token else None


def save_token(token: str, base_url: str) -> Path:
    """Persist ``token`` for the API at ``base_url``, readable by the owner only."""
    tokens = _read_saved_tokens()
    tokens[_token_key(base_url)] = token.strip()
    return _write_saved_tokens(tokens)


def clear_saved_token(base_url: Optional[str] = None) -> bool:
    """Forget the token for ``base_url`` (all saved tokens when None). True if any existed."""
    tokens = _read_saved_tokens()
    if base_url is None:
        existed = bool(tokens)
        tokens = {}
    else:
        existed = tokens.pop(_token_key(base_url), None) is not None
    _write_saved_tokens(tokens)
    return existed
