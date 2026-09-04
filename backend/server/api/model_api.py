from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.response import ResponseUtil
from server.auth_dependencies import get_current_user
from noesis.llm.catalog import get_catalog_vision_meta, get_default_model_id, list_public_models
from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.model_vo import (
    ModelCatalogItem,
    ModelCatalogResponse,
    PlatformProviderInfo,
    ProviderPresetItem,
)
from noesis.services.user_llm_service import UserLLMService

model_router = APIRouter(prefix="/api/models")


def _platform_provider_info():
    """内置目录的平台 Provider 元数据：id=type，label 与目录行同一规则（见 catalog）。"""
    from noesis.config.env import ModelConfig
    from noesis.llm.catalog import provider_display_label

    return PlatformProviderInfo(
        id=ModelConfig.model_type,
        label=provider_display_label(ModelConfig.model_type, ModelConfig.model_base_url),
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
