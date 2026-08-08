"""Re-export scheduled-task ORM from ``noesis.storage`` (transition shim)."""
from __future__ import annotations

from noesis.storage.postgres.models.scheduled_task import TUserScheduledTask

__all__ = ["TUserScheduledTask"]
