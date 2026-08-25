"""Terminal Run eligibility and idempotent capture-job creation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import MachineMemoryConfig
from noesis.repositories.machine_memory_repository import MachineMemoryRepository
from noesis.repositories.memory_preference_repository import MemoryPreferenceRepository
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.chat import TAgentRun


_TERMINAL_STATUSES = {"completed", "partial", "error", "interrupted"}
_TERMINAL_TOOL_STATES = {"completed", "success", "error", "failed", "rejected", "cancelled", "timeout"}


def has_stable_work(content: dict[str, Any]) -> bool:
    parts = content.get("parts")
    if not isinstance(parts, list):
        return False
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        if part_type == "text" and str(part.get("content") or part.get("text") or "").strip():
            return True
        if part_type == "tool":
            state = str(part.get("state") or part.get("status") or "").casefold()
            has_outcome = any(part.get(key) not in (None, "", {}) for key in ("output", "error", "outcome", "exit_code"))
            if state in _TERMINAL_TOOL_STATES and has_outcome:
                return True
        if part_type in {"artifact", "file"} and any(
            part.get(key) for key in ("digest", "path", "artifact_id")
        ):
            return True
    return False


class MemoryCaptureService:
    @staticmethod
    async def record_recalled_bulletin(
        *,
        run_id: str,
        user_id: str,
        memory_ids: tuple[str, ...],
        bulletin_hash: str,
        degraded: bool,
        source_snapshot_digest: str,
    ) -> None:
        async with pg_manager.get_async_session_context() as db:
            result = await db.execute(
                update(TAgentRun)
                .where(TAgentRun.id == run_id, TAgentRun.user_id == str(user_id))
                .values(memory_context={
                    "memory_ids": sorted(set(memory_ids)),
                    "bulletin_hash": bulletin_hash,
                    "degraded": degraded,
                    "source_snapshot_digest": source_snapshot_digest,
                })
            )
            if result.rowcount != 1:
                raise LookupError("Run recall context target is unavailable")
            await db.commit()

    @staticmethod
    async def enqueue_for_terminal(
        db: AsyncSession,
        *,
        run_id: str,
        content: dict[str, Any],
        max_attempts: int | None = None,
    ) -> bool:
        repository = MachineMemoryRepository(db)
        context = await repository.capture_context(run_id)
        if (
            context is None
            or context.status not in _TERMINAL_STATUSES
            or context.session_kind != "root"
            or context.origin == "memory"
            or not has_stable_work(content)
        ):
            return False
        if not await MemoryPreferenceRepository(db).is_enabled(context.user_id):
            return False
        return await repository.enqueue_capture_job(
            context,
            max_attempts=max_attempts or MachineMemoryConfig.job_max_attempts,
        )


__all__ = ["MemoryCaptureService", "has_stable_work"]
