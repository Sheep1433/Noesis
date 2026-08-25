"""Persistence for the single user-controlled machine-memory switch."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.storage.postgres.models.memory import TMemoryUserPreference


class MemoryPreferenceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(
        self, user_id: str, *, for_update: bool = False
    ) -> TMemoryUserPreference | None:
        if not for_update:
            return await self.db.get(TMemoryUserPreference, str(user_id))
        return (
            await self.db.execute(
                select(TMemoryUserPreference)
                .where(TMemoryUserPreference.user_id == str(user_id))
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def set(self, *, user_id: str, enabled: bool) -> TMemoryUserPreference:
        await self.db.execute(
            pg_insert(TMemoryUserPreference)
            .values(user_id=str(user_id), enabled=enabled)
            .on_conflict_do_update(
                index_elements=[TMemoryUserPreference.user_id],
                set_={
                    "enabled": enabled,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
        )
        preference = await self.db.get(
            TMemoryUserPreference, str(user_id), populate_existing=True
        )
        if preference is None:
            raise RuntimeError("memory preference upsert did not return a row")
        return preference

    async def is_enabled(self, user_id: str, *, for_update: bool = False) -> bool:
        row = await self.get(str(user_id), for_update=for_update)
        return bool(row and row.enabled)


__all__ = ["MemoryPreferenceRepository"]
