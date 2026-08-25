"""User-scoped notification policy; disabling notification never stops the business run."""

from __future__ import annotations

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.repositories.settings_repository import SettingsRepository
from noesis.storage.postgres.models.settings import TUserNotificationPreference
from noesis.services.settings_service import SettingsService

EVENT_TYPES = frozenset({"automation.succeeded", "automation.failed", "hitl.pending", "channel.unavailable"})
SURFACES = frozenset({"web", "channel"})


class NotificationPreferenceService:
    @staticmethod
    def _view(row: TUserNotificationPreference) -> dict:
        return {"event_type": row.event_type, "delivery_surface": row.delivery_surface, "enabled": row.enabled, "version": row.version, "updated_at": row.updated_at}

    @classmethod
    async def list_preferences(cls, db: AsyncSession, user_id: str) -> list[dict]:
        rows = await SettingsRepository(db).list_notification_preferences(user_id)
        indexed = {(row.event_type, row.delivery_surface): row for row in rows}
        return [cls._view(indexed[(event, surface)]) if (event, surface) in indexed else {"event_type": event, "delivery_surface": surface, "enabled": True, "version": 0, "updated_at": None} for event in sorted(EVENT_TYPES) for surface in sorted(SURFACES)]

    @classmethod
    async def set_preference(cls, db: AsyncSession, user_id: str, event_type: str, surface: str, enabled: bool, expected_version: int | None = None) -> dict:
        if event_type not in EVENT_TYPES or surface not in SURFACES:
            raise ValueError("不支持的通知类型或接收方式")
        repo = SettingsRepository(db)
        row = await repo.get_notification_preference(user_id, event_type, surface)
        now = int(time.time() * 1000)
        if row is None:
            row = TUserNotificationPreference(id=str(uuid.uuid4()), user_id=user_id, event_type=event_type, delivery_surface=surface, enabled=enabled, version=1, created_at=now, updated_at=now)
            db.add(row)
        else:
            if expected_version is not None and row.version != expected_version:
                raise RuntimeError("通知设置已更新，请刷新后重试")
            row.enabled, row.version, row.updated_at = enabled, row.version + 1, now
        await SettingsService.append_audit(db, user_id=user_id, action="notification.update", setting_domain="notification", target_id=f"{event_type}:{surface}", summary={"enabled": enabled})
        await db.commit()
        await db.refresh(row)
        return cls._view(row)

    @staticmethod
    async def should_notify(db: AsyncSession, user_id: str, event_type: str, surface: str) -> bool:
        row = await SettingsRepository(db).get_notification_preference(user_id, event_type, surface)
        return True if row is None else bool(row.enabled)
