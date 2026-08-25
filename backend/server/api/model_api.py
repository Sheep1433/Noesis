from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.response import ResponseUtil
from server.auth_dependencies import get_current_user, require_csrf
from noesis.llm.catalog import get_catalog_vision_meta, get_default_model_id, list_public_models
from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.model_vo import (
    ModelCatalogItem,
    ModelCatalogResponse,
    PlatformProviderInfo,
    ProviderPresetItem,
)
from noesis.services.settings_service import SettingsService
from noesis.services.user_llm_service import UserLLMService

model_router = APIRouter(prefix="/api/models")


def _platform_provider_info():
    """内置目录的平台 Provider 元数据：id=type，label 取预设名回退 type。"""
    from noesis.config.env import ModelConfig

    label = ModelConfig.model_type
    for preset in ModelConfig.provider_presets:
        if preset.get("id") == ModelConfig.model_type:
            label = str(preset.get("label") or label)
            break
    return PlatformProviderInfo(
        id=ModelConfig.model_type,
        label=label,
        base_url=ModelConfig.model_base_url,
    )


@model_router.get("", summary="可选对话模型目录（内置 + 用户自定义）")
async def list_chat_models(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    models = [ModelCatalogItem.model_validate(item) for item in list_public_models()]
    custom_rows = await UserLLMService.list_models(db, user_id=current_user.user_id)
    models.extend(
        ModelCatalogItem.model_validate(row) for row in UserLLMService.public_model_rows(custom_rows)
    )
    vision_meta = get_catalog_vision_meta()
    from noesis.config.env import ModelConfig

    # 默认模型：用户偏好优先（可指向内置或自定义复合 id），回退 yaml 目录默认
    default_id = get_default_model_id()
    user_default = await UserLLMService.get_default_model(
        db, user_id=current_user.user_id
    )
    if user_default:
        model_ids = {m.id for m in models}
        default_id = user_default if user_default in model_ids else default_id
    return ResponseUtil.success(
        data=ModelCatalogResponse(
            platform_provider=_platform_provider_info(),
            models=models,
            provider_presets=[
                ProviderPresetItem.model_validate(preset)
                for preset in ModelConfig.provider_presets
            ],
            default_id=default_id,
            first_vision_model_id=vision_meta.get("first_vision_model_id"),
            vlm_fallback_available=bool(vision_meta.get("vlm_fallback_available")),
        ).model_dump(),
    )


@model_router.post("/discover-platform", summary="发现平台 Provider 当前可用模型")
async def discover_platform_models(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用内置目录的平台端点 + 平台 Key（opencode 回退 public）探测 ``GET /models``。

    OpenCode Zen 免费模型会轮换，内置 yaml 目录可能滞后；本端点返回当前
    真实可用列表，前端供用户采纳为自定义模型（不改动部署侧 yaml 目录）。
    """
    await require_csrf(request)
    from noesis.config.env import ModelConfig

    result = await UserLLMService._probe_models_endpoint(
        ModelConfig.model_base_url, ModelConfig.model_api_key
    )
    # zen 端点 /models 不含上下文窗口；从 yaml 内置目录合并已知值
    if result.get("ok"):
        from noesis.llm.catalog import get_model_catalog

        known = {
            entry.id: entry.context_window
            for entry in get_model_catalog()
            if entry.context_window
        }
        for row in result.get("models") or []:
            if not row.get("context_window"):
                window = known.get(row["model_id"])
                if window:
                    row["context_window"] = window
                    row["context_source"] = "catalog"
    await SettingsService.append_audit(
        db,
        user_id=current_user.user_id,
        action="llm.platform_provider.discover",
        setting_domain="llm",
        target_id=ModelConfig.model_type,
        summary={"status": result.get("status"), "model_count": len(result.get("models") or [])},
    )
    await db.commit()
    return ResponseUtil.success(data=result)
