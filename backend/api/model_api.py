from fastapi import APIRouter

from common.http.response import ResponseUtil
from llm.catalog import get_default_model_id, list_public_models
from schemas.model_vo import ModelCatalogItem, ModelCatalogResponse

model_router = APIRouter(prefix="/api/models")


@model_router.get("", summary="可选对话模型目录")
async def list_chat_models():
    models = [ModelCatalogItem.model_validate(item) for item in list_public_models()]
    return ResponseUtil.success(
        data=ModelCatalogResponse(
            models=models,
            default_id=get_default_model_id(),
        ).model_dump(),
    )
