"""设置控制面应用服务。"""

from __future__ import annotations

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.yaml_config import load_app_yaml
from noesis.security.secrets import redact_sensitive
from noesis.repositories.settings_repository import SettingsRepository
from noesis.storage.postgres.models.settings import TUserSettingsAudit
from noesis_server.schemas.settings_vo import SettingsAuditItem, SettingsAuditPage, SettingsCapabilities


class SettingsService:
    @classmethod
    def get_capabilities(cls) -> SettingsCapabilities:
        flags = load_app_yaml().settings_features
        return SettingsCapabilities.model_validate(flags.model_dump())

    @classmethod
    async def append_audit(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        action: str,
        setting_domain: str,
        target_id: str | None = None,
        summary: dict | None = None,
        correlation_id: str | None = None,
    ) -> TUserSettingsAudit:
        row = TUserSettingsAudit(
            id=str(uuid.uuid4()),
            user_id=user_id,
            action=action,
            setting_domain=setting_domain,
            target_id=target_id,
            summary=redact_sensitive(summary or {}),
            correlation_id=correlation_id,
            created_at=int(time.time() * 1000),
        )
        await SettingsRepository(db).append_audit(row)
        return row

    @classmethod
    async def list_audit(cls, db: AsyncSession, user_id: int, page: int, page_size: int) -> SettingsAuditPage:
        rows, total = await SettingsRepository(db).list_audit(user_id, (page - 1) * page_size, page_size)
        return SettingsAuditPage(
            items=[SettingsAuditItem.model_validate(row, from_attributes=True) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
        )
