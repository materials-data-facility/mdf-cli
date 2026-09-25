"""Global configuration for MDF Agent CLI.

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
    """Where ``mdf login --token --save`` keeps a raw access token (mode 0600).

    Deliberately separate from ``config.json`` (which ``mdf config show``
    prints) and from the Globus token store (``tokens.json``, owned by
    globus_sdk and keyed by resource server).
    """
    return _default_config_path().parent / "access_token"


def load_saved_token() -> Optional[str]:
    """The saved access token, or None when there is none (or it is unreadable)."""
    path = saved_token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def save_token(token: str) -> Path:
    """Persist ``token`` for later CLI/SDK calls, readable by the owner only."""
    path = saved_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token.strip() + "\n")
    os.chmod(path, 0o600)
    return path


def clear_saved_token() -> bool:
    """Delete the saved access token. Returns True if one existed."""
    path = saved_token_path()
    if path.exists():
        path.unlink()
        return True
    return False
