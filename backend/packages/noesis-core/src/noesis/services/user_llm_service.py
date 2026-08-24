"""用户自定义对话模型服务：provider/model CRUD、密钥加密存取、运行时解析。

密钥安全约定：
- 明文只在接受请求的那一瞬间存在，落库前经 ``SecretCipher`` 加密（``enc:`` 前缀）；
- 对外（API/日志/审计）只暴露 ``has_key`` 与尾部片段，永不回传明文；
- 更新走 keep/replace/clear 三态，避免编辑表单回填明文；
- 解密仅发生在 ``resolve_runtime_snapshots``，结果只进内存 ContextVar。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.secrets import SecretCipher, SecretEncryptionUnavailable, secret_suffix
from noesis.errors.exceptions import ConflictException, NotFoundException, ServiceException
from noesis.llm.runtime_snapshot import RuntimeModelSnapshot
from noesis.storage.postgres.models.user_llm import TUserLLMModel, TUserLLMProvider

_ALLOWED_API_TYPES = {"openai", "deepseek", "qwen", "minimax", "opencode"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _encrypt_api_key(raw: str) -> tuple[Optional[str], Optional[str]]:
    """加密 key，返回 (密文, 尾部片段)。未配置加密密钥时抛错，绝不降级明文。"""
    token = raw.strip()
    if not token:
        return None, None
    try:
        cipher_text = SecretCipher().encrypt(token)
    except SecretEncryptionUnavailable:
        raise ServiceException(
            data={"code": "secret_encryption_unavailable"},
            message="敏感配置暂时无法保存：未配置 SETTINGS_ENCRYPTION_KEY",
        )
    return f"enc:{cipher_text}", secret_suffix(token)


def _decrypt_api_key(cipher_text: Optional[str]) -> str:
    if not cipher_text or not cipher_text.startswith("enc:"):
        return ""
    return SecretCipher().decrypt(cipher_text[len("enc:") :])


class UserLLMService:
    """用户自定义对话模型的存取与解析。"""

    # ---------- 校验 ----------

    @staticmethod
    def _validate_api_type(api_type: str) -> str:
        normalized = str(api_type or "").strip().lower()
        if normalized not in _ALLOWED_API_TYPES:
            raise ServiceException(
                message=f"不支持的协议类型: {api_type}，可选 {sorted(_ALLOWED_API_TYPES)}"
            )
        return normalized

    @staticmethod
    async def _ensure_model_id_free(
        db: AsyncSession, user_id: int, model_id: str, exclude_entry_id: Optional[str] = None
    ) -> None:
        from noesis.llm.catalog import get_model_catalog

        for entry in get_model_catalog():
            if entry.id == model_id:
                raise ConflictException(message=f"模型 ID 与内置目录冲突: {model_id}")
        cond = [
            TUserLLMModel.user_id == user_id,
            TUserLLMModel.model_id == model_id,
            TUserLLMModel.deleted_at.is_(None),
        ]
        if exclude_entry_id:
            cond.append(TUserLLMModel.id != exclude_entry_id)
        result = await db.execute(select(TUserLLMModel.id).where(and_(*cond)).limit(1))
        if result.scalar_one_or_none() is not None:
            raise ConflictException(message=f"已存在同 ID 的自定义模型: {model_id}")

    # ---------- Provider CRUD ----------

    @staticmethod
    async def create_provider(
        db: AsyncSession, *, user_id: int, name: str, api_type: str, base_url: str,
        api_key: str, enabled: bool = True,
    ) -> Dict[str, Any]:
        now = _now_ms()
        cipher, suffix = _encrypt_api_key(api_key)
        if not cipher:
            raise ServiceException(message="API Key 不能为空")
        provider = TUserLLMProvider(
            id=str(uuid.uuid4()), user_id=user_id,
            name=name.strip() or "未命名服务", api_type=UserLLMService._validate_api_type(api_type),
            base_url=base_url.strip().rstrip("/"), api_key_cipher=cipher, api_key_suffix=suffix,
            enabled=enabled, created_at=now, updated_at=now,
        )
        db.add(provider)
        await db.commit()
        return UserLLMService._provider_view(provider)

    @staticmethod
    async def update_provider(
        db: AsyncSession, *, user_id: int, provider_id: str,
        name: Optional[str] = None, api_type: Optional[str] = None,
        base_url: Optional[str] = None, enabled: Optional[bool] = None,
        api_key: Optional[str] = None, api_key_action: str = "keep",
    ) -> Dict[str, Any]:
        provider = await UserLLMService._get_provider(db, user_id, provider_id)
        now = _now_ms()
        if name is not None and name.strip():
            provider.name = name.strip()
        if api_type is not None:
            provider.api_type = UserLLMService._validate_api_type(api_type)
        if base_url is not None and base_url.strip():
            provider.base_url = base_url.strip().rstrip("/")
        if enabled is not None:
            provider.enabled = bool(enabled)
        if api_key_action == "replace":
            if not (api_key or "").strip():
                raise ServiceException(message="替换 API Key 时必须填写新值")
            provider.api_key_cipher, provider.api_key_suffix = _encrypt_api_key(api_key)
        elif api_key_action == "clear":
            provider.api_key_cipher, provider.api_key_suffix = None, None
        elif api_key_action != "keep":
            raise ServiceException(message=f"不支持的 Key 写入动作: {api_key_action}")
        provider.updated_at = now
        await db.commit()
        return UserLLMService._provider_view(provider)

    @staticmethod
    async def delete_provider(db: AsyncSession, *, user_id: int, provider_id: str) -> None:
        provider = await UserLLMService._get_provider(db, user_id, provider_id)
        now = _now_ms()
        await db.execute(
            update(TUserLLMProvider)
            .where(TUserLLMProvider.id == provider_id)
            .values(deleted_at=now, api_key_cipher=None, api_key_suffix=None)
        )
        await db.execute(
            update(TUserLLMModel)
            .where(and_(TUserLLMModel.provider_id == provider_id, TUserLLMModel.deleted_at.is_(None)))
            .values(deleted_at=now)
        )
        provider.updated_at = now
        await db.commit()

    @staticmethod
    async def list_providers(db: AsyncSession, *, user_id: int) -> List[Dict[str, Any]]:
        result = await db.execute(
            select(TUserLLMProvider)
            .where(and_(TUserLLMProvider.user_id == user_id, TUserLLMProvider.deleted_at.is_(None)))
            .order_by(TUserLLMProvider.created_at)
        )
        return [UserLLMService._provider_view(p) for p in result.scalars().all()]

    @staticmethod
    async def _get_provider(db: AsyncSession, user_id: int, provider_id: str) -> TUserLLMProvider:
        result = await db.execute(
            select(TUserLLMProvider).where(and_(
                TUserLLMProvider.id == provider_id,
                TUserLLMProvider.user_id == user_id,
                TUserLLMProvider.deleted_at.is_(None),
            ))
        )
        provider = result.scalar_one_or_none()
        if provider is None:
            raise NotFoundException(message="模型服务不存在")
        return provider

    @staticmethod
    def _provider_view(provider: TUserLLMProvider) -> Dict[str, Any]:
        return {
            "provider_id": provider.id,
            "name": provider.name,
            "api_type": provider.api_type,
            "base_url": provider.base_url,
            "enabled": provider.enabled,
            "has_key": bool(provider.api_key_cipher),
            "api_key_masked": f"***{provider.api_key_suffix}" if provider.api_key_suffix else None,
        }

    # ---------- Model CRUD ----------

    @staticmethod
    async def create_model(
        db: AsyncSession, *, user_id: int, provider_id: str, model_id: str,
        label: str = "", temperature: Optional[float] = None, context_window: int = 0,
    ) -> Dict[str, Any]:
        await UserLLMService._get_provider(db, user_id, provider_id)
        normalized = str(model_id or "").strip()
        if not normalized:
            raise ServiceException(message="模型 ID 不能为空")
        await UserLLMService._ensure_model_id_free(db, user_id, normalized)
        now = _now_ms()
        entry = TUserLLMModel(
            id=str(uuid.uuid4()), user_id=user_id, provider_id=provider_id,
            model_id=normalized, label=(label or "").strip() or normalized,
            temperature=temperature, context_window=int(context_window or 0),
            created_at=now, updated_at=now,
        )
        db.add(entry)
        await db.commit()
        return UserLLMService._model_view(db_entry=entry, api_type=None)

    @staticmethod
    async def update_model(
        db: AsyncSession, *, user_id: int, entry_id: str,
        provider_id: Optional[str] = None, model_id: Optional[str] = None,
        label: Optional[str] = None, temperature: Optional[float] = None,
        context_window: Optional[int] = None,
    ) -> Dict[str, Any]:
        entry = await UserLLMService._get_model(db, user_id, entry_id)
        if provider_id is not None and provider_id != entry.provider_id:
            await UserLLMService._get_provider(db, user_id, provider_id)
            entry.provider_id = provider_id
        if model_id is not None:
            normalized = str(model_id or "").strip()
            if not normalized:
                raise ServiceException(message="模型 ID 不能为空")
            if normalized != entry.model_id:
                await UserLLMService._ensure_model_id_free(db, user_id, normalized, exclude_entry_id=entry.id)
                entry.model_id = normalized
        if label is not None and label.strip():
            entry.label = label.strip()
        if temperature is not None:
            entry.temperature = temperature
        if context_window is not None:
            entry.context_window = int(context_window or 0)
        entry.updated_at = _now_ms()
        await db.commit()
        return UserLLMService._model_view(db_entry=entry, api_type=None)

    @staticmethod
    async def delete_model(db: AsyncSession, *, user_id: int, entry_id: str) -> None:
        entry = await UserLLMService._get_model(db, user_id, entry_id)
        entry.deleted_at = _now_ms()
        await db.commit()

    @staticmethod
    async def list_models(db: AsyncSession, *, user_id: int) -> List[Dict[str, Any]]:
        rows = await UserLLMService._load_models(db, user_id)
        return [
            UserLLMService._model_view(db_entry=m, api_type=p.api_type if p else None)
            for m, p in rows
        ]

    @staticmethod
    async def _load_models(db: AsyncSession, user_id: int):
        result = await db.execute(
            select(TUserLLMModel, TUserLLMProvider)
            .outerjoin(
                TUserLLMProvider,
                and_(TUserLLMProvider.id == TUserLLMModel.provider_id, TUserLLMProvider.deleted_at.is_(None)),
            )
            .where(and_(TUserLLMModel.user_id == user_id, TUserLLMModel.deleted_at.is_(None)))
            .order_by(TUserLLMModel.created_at)
        )
        return result.all()

    @staticmethod
    async def _get_model(db: AsyncSession, user_id: int, entry_id: str) -> TUserLLMModel:
        result = await db.execute(
            select(TUserLLMModel).where(and_(
                TUserLLMModel.id == entry_id,
                TUserLLMModel.user_id == user_id,
                TUserLLMModel.deleted_at.is_(None),
            ))
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            raise NotFoundException(message="模型条目不存在")
        return entry

    @staticmethod
    def _model_view(*, db_entry: TUserLLMModel, api_type: Optional[str]) -> Dict[str, Any]:
        return {
            "entry_id": db_entry.id,
            "provider_id": db_entry.provider_id,
            "api_type": api_type,
            "model_id": db_entry.model_id,
            "label": db_entry.label,
            "temperature": db_entry.temperature,
            "context_window": db_entry.context_window,
        }

    # ---------- 目录合并与运行时解析 ----------

    @staticmethod
    def public_model_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """自定义模型转成前端目录行（与内置 list_public_models 同构）。"""
        return [
            {
                "id": row["model_id"],
                "label": row["label"],
                "model_type": row["api_type"],
                "is_default": False,
                "supports_vision": False,
                "context_window": row["context_window"],
                "custom": True,
            }
            for row in rows
        ]

    @staticmethod
    async def resolve_runtime_snapshots(
        db: AsyncSession, *, user_id: int, model_id: Optional[str]
    ) -> List[RuntimeModelSnapshot]:
        """把用户自定义模型解析为运行时快照（含解密 key），供 ContextVar 注入。

        命中自定义模型时返回单元素列表；未命中返回空列表，调用方回退内置目录。
        """
        normalized = str(model_id or "").strip()
        if not normalized:
            return []
        result = await db.execute(
            select(TUserLLMModel, TUserLLMProvider)
            .join(
                TUserLLMProvider,
                and_(TUserLLMProvider.id == TUserLLMModel.provider_id, TUserLLMProvider.deleted_at.is_(None)),
            )
            .where(and_(
                TUserLLMModel.user_id == user_id,
                TUserLLMModel.model_id == normalized,
                TUserLLMModel.deleted_at.is_(None),
            ))
            .limit(1)
        )
        row = result.first()
        if row is None:
            return []
        entry, provider = row
        if not provider.enabled:
            raise ServiceException(message=f"模型服务「{provider.name}」已停用")
        return [
            RuntimeModelSnapshot(
                id=entry.model_id,
                provider_id=provider.id,
                purpose="chat",
                model_type=provider.api_type,
                base_url=provider.base_url,
                api_key=_decrypt_api_key(provider.api_key_cipher),
                label=entry.label,
                context_window=entry.context_window,
            )
        ]

    # ---------- 连通测试 ----------

    @staticmethod
    async def test_provider(db: AsyncSession, *, user_id: int, provider_id: str) -> Dict[str, Any]:
        provider = await UserLLMService._get_provider(db, user_id, provider_id)
        api_key = _decrypt_api_key(provider.api_key_cipher)
        if not api_key:
            return {"ok": False, "message": "未配置 API Key"}
        from noesis.llm.factory import build_chat_model

        try:
            llm = build_chat_model(
                model_type=provider.api_type,
                model_name="probe-invalid-model",
                temperature=0.0,
                model_base_url=provider.base_url,
                model_api_key=api_key,
                provider_max_retries=0,
            )
            from langchain_core.messages import HumanMessage

            await llm.ainvoke([HumanMessage(content="ping")])
            return {"ok": True, "message": "连接正常"}
        except Exception as exc:  # noqa: BLE001 - 测试端点需要把根因归类回报
            message = str(exc)
            if "MODEL_API_KEY" in message:
                return {"ok": False, "message": "Key 解析失败"}
            # 端点可达但模型名探测失败（404/400 等）视为连通成功
            lowered = message.lower()
            if any(k in lowered for k in ("model", "404", "400", "not found", "invalid")):
                return {"ok": True, "message": "端点连通（探测模型名的报错可忽略）"}
            return {"ok": False, "message": message[:200]}
