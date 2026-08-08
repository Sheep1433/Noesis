"""Re-export knowledge-base ORM from ``noesis.storage`` (transition shim)."""
from __future__ import annotations

from noesis.storage.postgres.models.knowledge import TKbCollectionConfig

__all__ = ["TKbCollectionConfig"]
