from fastapi import APIRouter

from common.http.response import ResponseUtil
from llm.catalog import get_catalog_vision_meta, get_default_model_id, list_public_models
from schemas.model_vo import ModelCatalogItem, ModelCatalogResponse

model_router = APIRouter(prefix="/api/models")


@model_router.get("", summary="可选对话模型目录")
async def list_chat_models():
    models = [ModelCatalogItem.model_validate(item) for item in list_public_models()]
    vision_meta = get_catalog_vision_meta()
    return ResponseUtil.success(
        data=ModelCatalogResponse(
            models=models,
            default_id=get_default_model_id(),
            first_vision_model_id=vision_meta.get("first_vision_model_id"),
            vlm_fallback_available=bool(vision_meta.get("vlm_fallback_available")),
        ).model_dump(),
    )
