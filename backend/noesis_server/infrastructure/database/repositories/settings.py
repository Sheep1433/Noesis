"""用户设置持久化；所有查询必须显式携带 user_id。"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.models.settings_models import (
    TUserModelPurposeBinding,
    TUserProviderConnection,
    TUserSettingsAudit,
    TUserNotificationPreference,
)


class SettingsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_provider(self, user_id: int, provider_id: str) -> TUserProviderConnection | None:
        result = await self.db.execute(
            select(TUserProviderConnection).where(
                TUserProviderConnection.user_id == user_id,
                TUserProviderConnection.id == provider_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_providers(self, user_id: int) -> list[TUserProviderConnection]:
        result = await self.db.execute(
            select(TUserProviderConnection)
            .where(TUserProviderConnection.user_id == user_id)
            .order_by(TUserProviderConnection.created_at.asc())
        )
        return list(result.scalars().all())

    async def add_provider(self, row: TUserProviderConnection) -> None:
        self.db.add(row)
        await self.db.flush()

    async def delete_provider(self, user_id: int, provider_id: str) -> bool:
        result = await self.db.execute(
            delete(TUserProviderConnection).where(
                TUserProviderConnection.user_id == user_id,
                TUserProviderConnection.id == provider_id,
            )
        )
        return result.rowcount == 1

    async def update_provider(
        self,
        user_id: int,
        provider_id: str,
        expected_version: int,
        values: dict,
    ) -> bool:
        safe_values = {**values, "version": expected_version + 1}
        result = await self.db.execute(
            update(TUserProviderConnection)
            .where(
                TUserProviderConnection.user_id == user_id,
                TUserProviderConnection.id == provider_id,
                TUserProviderConnection.version == expected_version,
            )
            .values(**safe_values)
        )
        return result.rowcount == 1

    async def get_binding(self, user_id: int, purpose: str) -> TUserModelPurposeBinding | None:
        result = await self.db.execute(
            select(TUserModelPurposeBinding).where(
                TUserModelPurposeBinding.user_id == user_id,
                TUserModelPurposeBinding.purpose == purpose,
            )
        )
        return result.scalar_one_or_none()

    async def list_bindings(self, user_id: int) -> list[TUserModelPurposeBinding]:
        result = await self.db.execute(
            select(TUserModelPurposeBinding)
            .where(TUserModelPurposeBinding.user_id == user_id)
            .order_by(TUserModelPurposeBinding.purpose.asc())
        )
        return list(result.scalars().all())

    async def delete_binding(self, user_id: int, purpose: str) -> None:
        await self.db.execute(
            delete(TUserModelPurposeBinding).where(
                TUserModelPurposeBinding.user_id == user_id,
                TUserModelPurposeBinding.purpose == purpose,
            )
        )

    async def delete_bindings_for_provider(self, user_id: int, provider_id: str) -> None:
        await self.db.execute(
            delete(TUserModelPurposeBinding).where(
                TUserModelPurposeBinding.user_id == user_id,
                TUserModelPurposeBinding.provider_id == provider_id,
            )
        )

    async def append_audit(self, row: TUserSettingsAudit) -> None:
        self.db.add(row)
        await self.db.flush()

    async def list_audit(self, user_id: int, offset: int, limit: int) -> tuple[list[TUserSettingsAudit], int]:
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

    async def list_notification_preferences(self, user_id: int) -> list[TUserNotificationPreference]:
        result = await self.db.execute(select(TUserNotificationPreference).where(TUserNotificationPreference.user_id == user_id).order_by(TUserNotificationPreference.event_type, TUserNotificationPreference.delivery_surface))
        return list(result.scalars().all())

    async def get_notification_preference(self, user_id: int, event_type: str, delivery_surface: str) -> TUserNotificationPreference | None:
        result = await self.db.execute(select(TUserNotificationPreference).where(TUserNotificationPreference.user_id == user_id, TUserNotificationPreference.event_type == event_type, TUserNotificationPreference.delivery_surface == delivery_surface))
        return result.scalar_one_or_none()
