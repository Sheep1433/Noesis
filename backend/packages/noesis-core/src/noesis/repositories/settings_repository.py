"""用户设置持久化；所有查询必须显式携带 user_id。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.storage.postgres.models.settings import (
    TUserSettingsAudit,
    TUserNotificationPreference,
)


class SettingsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def append_audit(self, row: TUserSettingsAudit) -> None:
        self.db.add(row)
        await self.db.flush()

    async def list_audit(self, user_id: str, offset: int, limit: int) -> tuple[list[TUserSettingsAudit], int]:
        total_result = await self.db.execute(
            select(func.count()).select_from(TUserSettingsAudit).where(TUserSettingsAudit.user_id == user_id)
        )
        rows_result = await self.db.execute(
            select(TUserSettingsAudit)
            .where(TUserSettingsAudit.user_id == user_id)
            .order_by(TUserSettingsAudit.created_at.desc(), TUserSettingsAudit.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows_result.scalars().all()), int(total_result.scalar_one())

    async def list_notification_preferences(self, user_id: str) -> list[TUserNotificationPreference]:
        result = await self.db.execute(select(TUserNotificationPreference).where(TUserNotificationPreference.user_id == user_id).order_by(TUserNotificationPreference.event_type, TUserNotificationPreference.delivery_surface))
        return list(result.scalars().all())

    async def get_notification_preference(self, user_id: str, event_type: str, delivery_surface: str) -> TUserNotificationPreference | None:
        result = await self.db.execute(select(TUserNotificationPreference).where(TUserNotificationPreference.user_id == user_id, TUserNotificationPreference.event_type == event_type, TUserNotificationPreference.delivery_surface == delivery_surface))
        return result.scalar_one_or_none()
