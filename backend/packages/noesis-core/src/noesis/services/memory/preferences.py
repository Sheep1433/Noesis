"""Per-user Memory Cortex desired state and effective gates."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.repositories.memory_preference_repository import MemoryPreferenceRepository
from noesis.schemas.memory import CortexPreferenceResponse
from noesis.storage.postgres.manager import pg_manager


class MemoryCortexPreferenceService:
    @staticmethod
    def _response(*, enabled: bool) -> CortexPreferenceResponse:
        return CortexPreferenceResponse(enabled=enabled)

    @classmethod
    async def get(
        cls, db: AsyncSession, *, user_id: str | int
    ) -> CortexPreferenceResponse:
        row = await MemoryPreferenceRepository(db).get(str(user_id))
        return cls._response(enabled=bool(row and row.enabled))

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        *,
        user_id: str | int,
        enabled: bool,
    ) -> CortexPreferenceResponse:
        row = await MemoryPreferenceRepository(db).set(
            user_id=str(user_id),
            enabled=enabled,
        )
        await db.commit()
        return cls._response(enabled=row.enabled)

    @staticmethod
    async def is_enabled(user_id: str) -> bool:
        async with pg_manager.get_async_session_context() as db:
            return await MemoryPreferenceRepository(db).is_enabled(str(user_id))


__all__ = ["MemoryCortexPreferenceService"]
