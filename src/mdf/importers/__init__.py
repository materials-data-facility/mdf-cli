"""External repository importers for cross-publishing datasets into MDF."""

from mdf.importers.registry import resolve_adapter

__all__ = ["resolve_adapter"]
