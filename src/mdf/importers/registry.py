"""Adapter registry — resolves identifiers to the appropriate adapter."""

from __future__ import annotations

from typing import Optional

from mdf.core.agent import _DOI_RE
from mdf.importers.base import ExternalRepoAdapter
from mdf.importers.datacite import DataCiteAdapter
from mdf.importers.zenodo import ZenodoAdapter


class _ZenodoDoiAdapter(ZenodoAdapter):
    """Let Zenodo's richer adapter accept the three supported DOI spellings."""

    def match(self, identifier: str) -> bool:
        match = _DOI_RE.match(identifier.strip())
        return bool(match and match.group(1).lower().startswith("10.5281/zenodo."))

    def parse_identifier(self, identifier: str) -> str:
        match = _DOI_RE.match(identifier.strip())
        if not match:
            raise ValueError(f"Cannot parse Zenodo DOI: {identifier}")
        suffix = match.group(1).split("zenodo.", 1)[-1]
        if not suffix.isdigit():
            raise ValueError(f"Cannot parse Zenodo DOI: {identifier}")
        return suffix

# Register adapters in priority order
_ADAPTERS = [
    ZenodoAdapter(),
    _ZenodoDoiAdapter(),
    DataCiteAdapter(),
]


def resolve_adapter(identifier: str) -> Optional[ExternalRepoAdapter]:
    """Return the first adapter that can handle the given identifier, or None."""
    for adapter in _ADAPTERS:
        if adapter.match(identifier):
            return adapter
    return None
