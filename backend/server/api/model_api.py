from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.response import ResponseUtil
from server.auth_dependencies import get_current_user
from noesis.llm.catalog import get_catalog_vision_meta, get_default_model_id, list_public_models
from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.model_vo import ModelCatalogItem, ModelCatalogResponse
from noesis.services.user_llm_service import UserLLMService

model_router = APIRouter(prefix="/api/models")


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
    return ResponseUtil.success(
        data=ModelCatalogResponse(
            models=models,
            default_id=get_default_model_id(),
            first_vision_model_id=vision_meta.get("first_vision_model_id"),
            vlm_fallback_available=bool(vision_meta.get("vlm_fallback_available")),
        ).model_dump(),
    )
