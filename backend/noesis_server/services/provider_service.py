"""用户 Provider、模型发现与用途绑定服务。"""

from __future__ import annotations

import time
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.common.security.secrets import SecretCipher, SecretEncryptionUnavailable, secret_suffix
from noesis_server.exceptions.exception import ConflictException, NotFoundException, ServiceException
from noesis_server.infrastructure.database.repositories.settings import SettingsRepository
from noesis_server.models.settings_models import TUserModelPurposeBinding, TUserProviderConnection
from noesis_server.schemas.settings_vo import (
    ModelPurposeBindingView,
    ModelPurposeBindingWrite,
    ProviderCreate,
    ProviderModel,
    ProviderProbeResult,
    ProviderUpdate,
    ProviderView,
    SecretSummary,
    SecretWriteAction,
)
from noesis_server.services.settings_service import SettingsService


def _now_ms() -> int:
    return int(time.time() * 1000)


def _capabilities(model_id: str) -> list[str]:
    name = model_id.lower()
    result: list[str] = []
    if any(token in name for token in ("embed", "bge", "e5-")):
        result.append("embedding")
    elif any(token in name for token in ("rerank", "ranker")):
        result.append("rerank")
    else:
        result.append("chat")
        if any(token in name for token in ("vision", "vl", "gpt-4o", "gemini", "qwen2.5-vl")):
            result.append("vision")
    return result


class ProviderService:
    @staticmethod
    def _view(row: TUserProviderConnection) -> ProviderView:
        return ProviderView(
            id=row.id,
            provider_type=row.provider_type,
            display_name=row.display_name,
            base_url=row.base_url,
            enabled=row.enabled,
            secret=SecretSummary(
                configured=bool(row.secret_ciphertext),
                suffix=row.secret_suffix,
                updated_at=str(row.secret_updated_at) if row.secret_updated_at else None,
            ),
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    async def list(cls, db: AsyncSession, user_id: int) -> list[ProviderView]:
        return [cls._view(row) for row in await SettingsRepository(db).list_providers(user_id)]

    @classmethod
    async def get_row(cls, db: AsyncSession, user_id: int, provider_id: str) -> TUserProviderConnection:
        row = await SettingsRepository(db).get_provider(user_id, provider_id)
        if row is None:
            raise NotFoundException(data="", message="Provider 不存在")
        return row

    @staticmethod
    def _encrypted_secret(action: SecretWriteAction, value: str | None, current: str | None = None):
        if action is SecretWriteAction.KEEP:
            return current, None, None
        if action is SecretWriteAction.CLEAR:
            return None, None, _now_ms()
        try:
            raw = (value or "").strip()
            return SecretCipher().encrypt(raw), secret_suffix(raw), _now_ms()
        except SecretEncryptionUnavailable as exc:
            raise ServiceException(data={"code": "secret_encryption_unavailable"}, message=str(exc)) from exc

    @classmethod
    async def create(cls, db: AsyncSession, user_id: int, body: ProviderCreate) -> ProviderView:
        now = _now_ms()
        ciphertext, suffix, secret_updated_at = cls._encrypted_secret(body.secret.action, body.secret.value)
        row = TUserProviderConnection(
            id=str(uuid.uuid4()), user_id=user_id, provider_type=body.provider_type,
            display_name=body.display_name.strip(), base_url=body.base_url.rstrip("/"), enabled=body.enabled,
            secret_ciphertext=ciphertext, secret_suffix=suffix, secret_updated_at=secret_updated_at,
            version=1, created_at=now, updated_at=now,
        )
        try:
            await SettingsRepository(db).add_provider(row)
            await SettingsService.append_audit(
                db, user_id=user_id, action="provider.create", setting_domain="provider", target_id=row.id,
                summary={"fields": ["provider_type", "display_name", "base_url", "enabled"], "secret_action": body.secret.action},
            )
            view = cls._view(row)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return view

    @classmethod
    async def update(cls, db: AsyncSession, user_id: int, provider_id: str, body: ProviderUpdate) -> ProviderView:
        current = await cls.get_row(db, user_id, provider_id)
        ciphertext, suffix, secret_updated_at = cls._encrypted_secret(body.secret.action, body.secret.value, current.secret_ciphertext)
        values = {
            "provider_type": body.provider_type, "display_name": body.display_name.strip(),
            "base_url": body.base_url.rstrip("/"), "enabled": body.enabled,
            "secret_ciphertext": ciphertext,
            "secret_suffix": current.secret_suffix if body.secret.action is SecretWriteAction.KEEP else suffix,
            "secret_updated_at": current.secret_updated_at if body.secret.action is SecretWriteAction.KEEP else secret_updated_at,
            "updated_at": _now_ms(),
        }
        await SettingsService.update_provider_with_audit(
            db, user_id=user_id, provider_id=provider_id, expected_version=body.version, values=values,
            summary={"fields": ["provider_type", "display_name", "base_url", "enabled"], "secret_action": body.secret.action},
        )
        return cls._view(await cls.get_row(db, user_id, provider_id))

    @classmethod
    async def delete(cls, db: AsyncSession, user_id: int, provider_id: str) -> None:
        await cls.get_row(db, user_id, provider_id)
        repo = SettingsRepository(db)
        try:
            await repo.delete_bindings_for_provider(user_id, provider_id)
            if not await repo.delete_provider(user_id, provider_id):
                raise ConflictException(data="", message="Provider 删除冲突，请刷新后重试")
            await SettingsService.append_audit(db, user_id=user_id, action="provider.delete", setting_domain="provider", target_id=provider_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    @classmethod
    async def _request_models(cls, row: TUserProviderConnection) -> list[ProviderModel]:
        if not row.enabled:
            raise ServiceException(data={"code": "provider_disabled"}, message="请先启用 Provider")
        if not row.secret_ciphertext:
            raise ServiceException(data={"code": "credential_missing"}, message="请先配置 Provider 凭据")
        try:
            token = SecretCipher().decrypt(row.secret_ciphertext)
            async with httpx.AsyncClient(timeout=httpx.Timeout(10), trust_env=False) as client:
                response = await client.get(f"{row.base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {token}"})
            if response.status_code in {401, 403}:
                raise ServiceException(data={"code": "authentication"}, message="Provider 凭据无效")
            response.raise_for_status()
            raw = response.json().get("data", [])
            return [ProviderModel(id=str(item["id"]), name=str(item.get("name") or item["id"]), capabilities=_capabilities(str(item["id"]))) for item in raw if item.get("id")]
        except ServiceException:
            raise
        except httpx.TimeoutException as exc:
            raise ServiceException(data={"code": "timeout"}, message="Provider 连接超时，请检查地址或网络") from exc
        except httpx.ConnectError as exc:
            raise ServiceException(data={"code": "connection"}, message="无法连接 Provider，请检查 Base URL") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ServiceException(data={"code": "invalid_response"}, message="Provider 返回了无法识别的响应") from exc

    @classmethod
    async def discover_models(cls, db: AsyncSession, user_id: int, provider_id: str) -> list[ProviderModel]:
        return await cls._request_models(await cls.get_row(db, user_id, provider_id))

    @classmethod
    async def probe(cls, db: AsyncSession, user_id: int, provider_id: str) -> ProviderProbeResult:
        checked_at = _now_ms()
        correlation_id = str(uuid.uuid4())
        try:
            models = await cls.discover_models(db, user_id, provider_id)
            return ProviderProbeResult(ok=True, checked_at=checked_at, error_category="none", message="连接正常", model_count=len(models), correlation_id=correlation_id)
        except ServiceException as exc:
            code = (exc.data or {}).get("code", "provider") if isinstance(exc.data, dict) else "provider"
            allowed = code if code in {"authentication", "timeout", "connection", "invalid_response"} else "provider"
            return ProviderProbeResult(ok=False, checked_at=checked_at, error_category=allowed, message=exc.message, correlation_id=correlation_id)

    @classmethod
    async def list_bindings(cls, db: AsyncSession, user_id: int) -> list[ModelPurposeBindingView]:
        rows = await SettingsRepository(db).list_bindings(user_id)
        return [ModelPurposeBindingView(purpose=r.purpose, provider_id=r.provider_id, model_id=r.model_id, model_name=r.model_name, capabilities=r.capabilities.get("values", []), version=r.version, updated_at=r.updated_at) for r in rows]

    @classmethod
    async def resolve_runtime_snapshot(cls, db: AsyncSession, user_id: int, purpose: str):
        """用户绑定存在且有效时返回不可变快照，否则由调用方回退平台目录。"""
        from noesis.llm.runtime_snapshot import RuntimeModelSnapshot

        binding = await SettingsRepository(db).get_binding(user_id, purpose)
        if binding is None:
            return None
        provider = await SettingsRepository(db).get_provider(user_id, binding.provider_id)
        if provider is None or not provider.enabled or not provider.secret_ciphertext:
            return None
        try:
            api_key = SecretCipher().decrypt(provider.secret_ciphertext)
        except (SecretEncryptionUnavailable, RuntimeError):
            return None
        return RuntimeModelSnapshot(
            id=f"user:{provider.id}:{binding.model_id}",
            provider_id=provider.id,
            purpose=purpose,
            model_type=provider.provider_type,
            model_name=binding.model_name,
            base_url=provider.base_url,
            api_key=api_key,
        )

    @classmethod
    async def resolve_runtime_snapshots(cls, db: AsyncSession, user_id: int):
        """一次读取并冻结当前 run 会使用的全部用户模型绑定。"""
        snapshots = []
        for purpose in ("chat", "vision", "embedding", "rerank"):
            snapshot = await cls.resolve_runtime_snapshot(db, user_id, purpose)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    @classmethod
    async def bind(cls, db: AsyncSession, user_id: int, purpose: str, body: ModelPurposeBindingWrite) -> ModelPurposeBindingView:
        if purpose not in {"chat", "vision", "embedding", "rerank"}:
            raise NotFoundException(data="", message="模型用途不存在")
        provider = await cls.get_row(db, user_id, body.provider_id)
        if not provider.enabled:
            raise ConflictException(data="", message="不能绑定已停用的 Provider")
        discovered = await cls._request_models(provider)
        selected = next((model for model in discovered if model.id == body.model_id), None)
        if selected is None:
            raise ConflictException(data={"code": "model_not_discovered"}, message="该模型不在 Provider 当前目录中")
        if purpose not in selected.capabilities:
            raise ConflictException(data={"code": "model_capability_mismatch"}, message=f"该模型不支持 {purpose} 用途")
        repo = SettingsRepository(db)
        old = await repo.get_binding(user_id, purpose)
        now = _now_ms()
        try:
            if old is None:
                row = TUserModelPurposeBinding(id=str(uuid.uuid4()), user_id=user_id, purpose=purpose, provider_id=body.provider_id, model_id=selected.id, model_name=selected.name, capabilities={"values": selected.capabilities}, version=1, created_at=now, updated_at=now)
                db.add(row)
            else:
                old.provider_id, old.model_id, old.model_name = body.provider_id, selected.id, selected.name
                old.capabilities, old.version, old.updated_at = {"values": selected.capabilities}, old.version + 1, now
                row = old
            await SettingsService.append_audit(db, user_id=user_id, action="model.binding.update", setting_domain="model", target_id=purpose, summary={"provider_id": body.provider_id, "model_id": selected.id, "capabilities": selected.capabilities})
            view = ModelPurposeBindingView(purpose=purpose, provider_id=row.provider_id, model_id=row.model_id, model_name=row.model_name, capabilities=selected.capabilities, version=row.version, updated_at=row.updated_at)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return view
