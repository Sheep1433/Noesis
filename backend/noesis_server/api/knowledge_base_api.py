"""Knowledge Base HTTP transport.

Business orchestration lives in ``noesis_server.services.knowledge_base_service``.
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.infrastructure.database.dependency import get_db
from noesis_server.schemas.knowledge_base_schema import (
    CreateCollectionRequest,
    PatchCollectionConfigRequest,
    SearchCollectionBody,
)
from noesis_server.schemas.login_vo import CurrentUser
from noesis_server.services import knowledge_base_service
from noesis_server.services.user_service import UserService


knowledge_base_router = APIRouter(prefix="/api/knowledge_base", tags=["知识库模块"])


@knowledge_base_router.get("/status")
async def get_status(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    return await knowledge_base_service.get_status(current_user)


@knowledge_base_router.get("/collections")
async def get_collections(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    return await knowledge_base_service.get_collections(current_user)


@knowledge_base_router.post("/collections")
async def create_collection(
    request: CreateCollectionRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await knowledge_base_service.create_collection(request, current_user, db)


@knowledge_base_router.delete("/collections/{collection_name}")
async def delete_collection(
    collection_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await knowledge_base_service.delete_collection(collection_name, current_user, db)


@knowledge_base_router.get("/collections/{collection_name}")
async def get_collection(
    collection_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    return await knowledge_base_service.get_collection(collection_name, current_user)


@knowledge_base_router.get("/collections/{collection_name}/config")
async def get_collection_config(
    collection_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await knowledge_base_service.get_collection_config(collection_name, current_user, db)


@knowledge_base_router.patch("/collections/{collection_name}/config")
async def patch_collection_config(
    collection_name: str,
    body: PatchCollectionConfigRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await knowledge_base_service.patch_collection_config(
        collection_name, body, current_user, db
    )


@knowledge_base_router.get("/collections/{collection_name}/documents")
async def get_documents(
    collection_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    return await knowledge_base_service.get_documents(collection_name, current_user)


@knowledge_base_router.get("/collections/{collection_name}/documents/{file_name}/shards")
async def get_shards(
    collection_name: str,
    file_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    return await knowledge_base_service.get_shards(collection_name, file_name, current_user)


@knowledge_base_router.get("/collections/{collection_name}/shards/{shard_id}")
async def get_shard_detail(
    collection_name: str,
    shard_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    return await knowledge_base_service.get_shard_detail(
        collection_name, shard_id, current_user
    )


@knowledge_base_router.delete("/collections/{collection_name}/documents/{file_name}")
async def delete_document(
    collection_name: str,
    file_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    return await knowledge_base_service.delete_document(
        collection_name, file_name, current_user
    )


@knowledge_base_router.post("/collections/{collection_name}/upload")
async def upload_document(
    collection_name: str,
    file: UploadFile = File(...),
    processing_params: Optional[str] = Form(
        None, description="可选 JSON：当次入库 processing_params 覆盖"
    ),
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    return await knowledge_base_service.upload_document(
        collection_name,
        file_name=file.filename or "unknown",
        content=content,
        processing_params=processing_params,
        current_user=current_user,
        db=db,
    )


@knowledge_base_router.post("/collections/{collection_name}/search")
async def search_collection(
    collection_name: str,
    body: SearchCollectionBody,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await knowledge_base_service.search_collection(
        collection_name, body, current_user, db
    )
