"""设置控制面应用服务。"""

from __future__ import annotations

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.yaml_config import load_app_yaml
from noesis_server.common.security.secrets import redact_sensitive
from noesis_server.exceptions.exception import ConflictException, NotFoundException
from noesis_server.infrastructure.database.repositories.settings import SettingsRepository
from noesis_server.models.settings_models import TUserSettingsAudit
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

    @classmethod
    async def update_provider_with_audit(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        provider_id: str,
        expected_version: int,
        values: dict,
        summary: dict,
    ) -> None:
        repo = SettingsRepository(db)
        if await repo.get_provider(user_id, provider_id) is None:
            raise NotFoundException(data="", message="Provider 不存在")
        try:
            changed = await repo.update_provider(user_id, provider_id, expected_version, values)
            if not changed:
                raise ConflictException(data="", message="设置已被其他请求更新，请刷新后重试")
            await cls.append_audit(
                db,
                user_id=user_id,
                action="provider.update",
                setting_domain="provider",
                target_id=provider_id,
                summary=summary,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
