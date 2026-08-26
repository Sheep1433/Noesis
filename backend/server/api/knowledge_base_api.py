"""Knowledge Base HTTP transport.

Service (``noesis.services.knowledge_base_service``) returns plain dicts and
raises domain exceptions; this layer wraps responses with ``ResponseUtil`` and
maps exceptions to HTTP status codes.
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession



from server.db import get_db

from noesis.schemas.knowledge_base_schema import (
    CreateCollectionRequest,
    PatchCollectionConfigRequest,
    SearchCollectionBody,
    ShardPageQuery,
)
from noesis.schemas.login_vo import CurrentUser
from noesis.services import knowledge_base_service
from server.auth_dependencies import get_current_user
from server.response import ResponseUtil
from noesis.errors.exceptions import (
    ConflictException,
    NotFoundException,
    ServiceException,
)
from noesis.knowledge.base import KBOperationError, QdrantNotConnectedError


knowledge_base_router = APIRouter(prefix="/api/knowledge_base", tags=["知识库模块"])


def _map_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc) or "请求参数无效")
    if isinstance(exc, QdrantNotConnectedError):
        return HTTPException(status_code=503, detail=str(exc.message or "向量库未连接"))
    if isinstance(exc, NotFoundException):
        return HTTPException(status_code=404, detail=exc.message or "资源不存在")
    if isinstance(exc, ConflictException):
        return HTTPException(status_code=409, detail=exc.message or "资源冲突")
    if isinstance(exc, ServiceException):
        return HTTPException(status_code=500, detail=exc.message or "服务异常")
    if isinstance(exc, KBOperationError):
        return HTTPException(status_code=500, detail=str(exc) or "知识库操作失败")
    return HTTPException(status_code=500, detail=str(exc))


@knowledge_base_router.get("/status")
async def get_status(
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        data = await knowledge_base_service.get_status(current_user)
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.get("/collections")
async def get_collections(
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        data = await knowledge_base_service.get_collections(current_user)
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.post("/collections")
async def create_collection(
    request: CreateCollectionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await knowledge_base_service.create_collection(request, current_user, db)
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.delete("/collections/{collection_name}")
async def delete_collection(
    collection_name: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await knowledge_base_service.delete_collection(collection_name, current_user, db)
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.get("/collections/{collection_name}")
async def get_collection(
    collection_name: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        data = await knowledge_base_service.get_collection(collection_name, current_user)
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.get("/collections/{collection_name}/config")
async def get_collection_config(
    collection_name: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await knowledge_base_service.get_collection_config(collection_name, current_user, db)
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.put("/collections/{collection_name}/config")
async def patch_collection_config(
    collection_name: str,
    body: PatchCollectionConfigRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await knowledge_base_service.patch_collection_config(
            collection_name, body, current_user, db
        )
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.get("/collections/{collection_name}/documents")
async def get_documents(
    collection_name: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        data = await knowledge_base_service.get_documents(collection_name, current_user)
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.get("/collections/{collection_name}/documents/{file_name}/shards")
async def get_shards(
    collection_name: str,
    file_name: str,
    limit: str = "20",
    cursor: Optional[str] = None,
    element_type: Optional[str] = None,
    locator_type: Optional[str] = None,
    keyword: Optional[str] = None,
    sort: str = "asc",
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        query = ShardPageQuery.model_validate(
            {
                "limit": limit,
                "cursor": cursor,
                "element_type": element_type,
                "locator_type": locator_type,
                "keyword": keyword,
                "sort": sort,
            }
        )
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "分页参数无效")
        raise HTTPException(status_code=400, detail=f"分页参数无效：{message}") from exc
    try:
        data = await knowledge_base_service.get_shards(
            collection_name, file_name, query, current_user
        )
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.get("/collections/{collection_name}/shards/{shard_id}")
async def get_shard_detail(
    collection_name: str,
    shard_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        data = await knowledge_base_service.get_shard_detail(
            collection_name, shard_id, current_user
        )
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.delete("/collections/{collection_name}/documents/{file_name}")
async def delete_document(
    collection_name: str,
    file_name: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        data = await knowledge_base_service.delete_document(
            collection_name, file_name, current_user
        )
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.post("/collections/{collection_name}/upload")
async def upload_document(
    collection_name: str,
    file: UploadFile = File(...),
    processing_params: Optional[str] = Form(
        None, description="可选 JSON：当次入库 processing_params 覆盖"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    try:
        data = await knowledge_base_service.upload_document(
            collection_name,
            file_name=file.filename or "unknown",
            content=content,
            processing_params=processing_params,
            current_user=current_user,
            db=db,
        )
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(data=data)


@knowledge_base_router.post("/collections/{collection_name}/search")
async def search_collection(
    collection_name: str,
    body: SearchCollectionBody,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await knowledge_base_service.search_collection(
            collection_name, body, current_user, db
        )
    except Exception as exc:
        raise _map_exception(exc) from exc
    return ResponseUtil.success(msg="检索成功", data=data)
