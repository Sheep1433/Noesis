"""
知识库管理 API：Qdrant 向量数据 + PostgreSQL 集合配置
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.login_vo import CurrentUser
from schemas.knowledge_base_schema import (
    CollectionInfo,
    CollectionDetail,
    CollectionConfigResponse,
    PatchCollectionConfigRequest,
    ShardDetail,
    KnowledgeBaseStatus,
    SearchResult,
    SearchTiming,
    SearchCollectionResponse,
    UploadResponse,
    DeleteResponse,
    CreateCollectionRequest,
    CreateCollectionResponse,
    SearchCollectionBody,
)
from services.qdrant_service import (
    QdrantService,
    is_qdrant_connected,
    get_qdrant_client,
)
from services.kb_collection_config_service import KbCollectionConfigService
from kb.chunk import (
    normalize_query_execution_params,
    resolve_effective_processing_params,
)
from kb.document_parse.staging import sanitize_kb_filename, write_staging
from config.env import QdrantConfig
from config.get_db import get_db
from common.http.response import ResponseUtil
from services.user_service import UserService


logger = logging.getLogger(__name__)
knowledge_base_router = APIRouter(prefix='/api/knowledge_base', tags=['知识库模块'])


async def _require_collection_in_qdrant(service: QdrantService, collection_name: str) -> dict:
    col_info = service.get_collection(collection_name)
    if not col_info:
        raise HTTPException(status_code=404, detail=f"Collection '{collection_name}' 不存在")
    return col_info


async def _require_collection_config(
    db: AsyncSession,
    service: QdrantService,
    collection_name: str,
) -> dict:
    await _require_collection_in_qdrant(service, collection_name)
    cfg = await KbCollectionConfigService.get_config(db, collection_name)
    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Collection '{collection_name}' 配置不存在",
        )
    return cfg


@knowledge_base_router.get('/status')
async def get_status(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """获取向量库连接状态"""
    _ = current_user
    connected = is_qdrant_connected()
    client = get_qdrant_client()
    collections_count = 0

    if connected and client:
        try:
            collections_count = len(client.get_collections().model_dump().get('collections', []))
        except Exception as e:
            logger.error(f"获取 collections 数量失败: {e}")

    return ResponseUtil.success(
        data=KnowledgeBaseStatus(
            connected=connected,
            host=QdrantConfig.qdrant_host,
            port=QdrantConfig.qdrant_port,
            collections_count=collections_count,
        ).model_dump(),
    )


@knowledge_base_router.get('/collections')
async def get_collections(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """获取所有 Collection 列表（数据来自 Qdrant）"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    collections = service.get_collections()

    items = [
        CollectionInfo(
            name=c.get("name", ""),
            vector_dimension=c.get("vector_dimension", 1024),
            documents_count=c.get("documents_count", 0),
            points_count=c.get("points_count", 0),
            created_at=c.get("created_at"),
        )
        for c in collections
        if c.get("name")
    ]
    return ResponseUtil.success(data=[item.model_dump() for item in items])


@knowledge_base_router.post('/collections')
async def create_collection(
    request: CreateCollectionRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建 Collection（Qdrant + PostgreSQL 默认配置）"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    result = service.create_collection(
        collection_name=request.name,
        vector_dimension=request.vector_dimension,
        description=request.description,
    )

    if not result['success']:
        http_code = int(result.get('code', 400))
        raise HTTPException(status_code=http_code, detail=result['message'])

    await KbCollectionConfigService.create_default(db, request.name)
    await db.commit()

    return ResponseUtil.success(
        data=CreateCollectionResponse(
            success=True,
            message=result['message'],
            name=result['name'],
        ).model_dump(),
    )


@knowledge_base_router.delete('/collections/{collection_name}')
async def delete_collection(
    collection_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 Collection（Qdrant + PostgreSQL 配置）"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    result = service.delete_collection(collection_name)

    if not result['success']:
        raise HTTPException(status_code=400, detail=result['message'])

    await KbCollectionConfigService.delete_config(db, collection_name)
    await db.commit()

    return ResponseUtil.success(data=result)


@knowledge_base_router.get('/collections/{collection_name}')
async def get_collection(
    collection_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """获取指定 Collection 详情（数据来自 Qdrant）"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    collection = service.get_collection(collection_name)

    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection '{collection_name}' 不存在")

    return ResponseUtil.success(
        data=CollectionDetail(
            name=collection.get("name", collection_name),
            vector_dimension=int(collection.get("vector_dimension", 1024)),
            documents_count=collection.get("documents_count", 0),
            points_count=collection.get("points_count", 0),
            created_at=collection.get("created_at"),
            status=collection.get("status"),
        ).model_dump(),
    )


@knowledge_base_router.get('/collections/{collection_name}/config')
async def get_collection_config(
    collection_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """读取集合 PostgreSQL 配置"""
    _ = current_user
    service = QdrantService()
    cfg = await _require_collection_config(db, service, collection_name)
    return ResponseUtil.success(data=CollectionConfigResponse(**cfg).model_dump())


@knowledge_base_router.patch('/collections/{collection_name}/config')
async def patch_collection_config(
    collection_name: str,
    body: PatchCollectionConfigRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """部分更新集合配置（deep-merge）"""
    _ = current_user
    service = QdrantService()
    await _require_collection_in_qdrant(service, collection_name)

    updated = await KbCollectionConfigService.patch_config(
        db,
        collection_name,
        processing_params=body.processing_params,
        query_params=body.query_params,
    )
    if updated is None:
        await KbCollectionConfigService.create_default(db, collection_name)
        updated = await KbCollectionConfigService.patch_config(
            db,
            collection_name,
            processing_params=body.processing_params,
            query_params=body.query_params,
        )
    await db.commit()
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Collection '{collection_name}' 配置不存在")
    return ResponseUtil.success(data=CollectionConfigResponse(**updated).model_dump())


@knowledge_base_router.get('/collections/{collection_name}/documents')
async def get_documents(
    collection_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """获取 Collection 下的文档列表"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    docs = service.get_collection_documents(collection_name)
    return ResponseUtil.success(data=docs)


@knowledge_base_router.get('/collections/{collection_name}/documents/{file_name}/shards')
async def get_shards(
    collection_name: str,
    file_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """获取文档的分片列表"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    shards = service.get_document_shards(collection_name, file_name)
    return ResponseUtil.success(data=shards)


@knowledge_base_router.get('/collections/{collection_name}/shards/{shard_id}')
async def get_shard_detail(
    collection_name: str,
    shard_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """获取分片详情"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    shard = service.get_shard_detail(collection_name, shard_id)

    if not shard:
        raise HTTPException(status_code=404, detail=f"分片 '{shard_id}' 不存在")

    return ResponseUtil.success(
        data=ShardDetail(
            id=str(shard["id"]),
            content=shard["content"],
            char_length=int(shard.get("char_length", 0)),
            vector_dimension=int(shard.get("vector_dimension", 0)),
            created_at=shard.get("created_at"),
            header_path=shard.get("header_path"),
            Header_1=shard.get("Header_1"),
            Header_2=shard.get("Header_2"),
            Header_3=shard.get("Header_3"),
            chunk_index=shard.get("chunk_index"),
            effective_processing_params=shard.get("effective_processing_params"),
        ).model_dump(),
    )


@knowledge_base_router.delete('/collections/{collection_name}/documents/{file_name}')
async def delete_document(
    collection_name: str,
    file_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """删除文档及其所有分片"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    result = service.delete_document(collection_name, file_name)

    if not result['success']:
        raise HTTPException(status_code=500, detail=result['message'])

    return ResponseUtil.success(
        data=DeleteResponse(
            success=result['success'],
            message=result['message'],
            deleted_count=result['deleted_count'],
        ).model_dump(),
    )


@knowledge_base_router.post('/collections/{collection_name}/upload')
async def upload_document(
    collection_name: str,
    file: UploadFile = File(...),
    processing_params: Optional[str] = Form(
        None, description="可选 JSON：当次入库 processing_params 覆盖"
    ),
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传文档：解析分块后写入 Qdrant。"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    request_once = None
    if processing_params and processing_params.strip():
        try:
            request_once = json.loads(processing_params)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"processing_params JSON 无效: {exc}") from exc

    original_name = sanitize_kb_filename(file.filename or "unknown")
    content = await file.read()
    staging_path, _file_hash = write_staging(collection_name, content, original_name)

    try:
        service = QdrantService()
        col_info = await _require_collection_in_qdrant(service, collection_name)
        vector_dim = int(col_info.get('vector_dimension', 1024))

        cfg = await KbCollectionConfigService.get_config(db, collection_name)
        collection_defaults = (cfg or {}).get("processing_params") or {}
        effective = resolve_effective_processing_params(
            collection_defaults=collection_defaults,
            request_once=request_once,
        )

        result = service.upload_document(
            collection_name=collection_name,
            file_name=original_name,
            file_path=str(staging_path),
            vector_dim=vector_dim,
            effective_processing_params=effective,
        )

        if not result['success']:
            code = int(result.get('code') or 500)
            if code == 409:
                raise HTTPException(status_code=409, detail=result['message'])
            raise HTTPException(status_code=500, detail=result['message'])

        return ResponseUtil.success(
            data=UploadResponse(
                success=True,
                message=result['message'],
                file_name=original_name,
                shards_created=result['shards_created'],
                extracted_markdown=result.get('extracted_markdown'),
            ).model_dump(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传文档失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}") from e
    finally:
        if staging_path.exists():
            staging_path.unlink()


@knowledge_base_router.post('/collections/{collection_name}/search')
async def search_collection(
    collection_name: str,
    body: SearchCollectionBody,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """知识库检索：hybrid 默认 + recall → rerank → final_top_k。"""
    _ = current_user
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    nb = body
    query = nb.query
    if not query or not str(query).strip():
        raise HTTPException(status_code=400, detail="查询文本不能为空")

    try:
        service = QdrantService()
        col_info = await _require_collection_in_qdrant(service, collection_name)
        vd = int(col_info.get("vector_dimension", 1024))

        cfg = await KbCollectionConfigService.get_config(db, collection_name)
        collection_query = (cfg or {}).get("query_params")

        raw_body = nb.model_dump(exclude_unset=True)
        overrides = {
            k: raw_body[k]
            for k in (
                "limit",
                "final_top_k",
                "recall_top_k",
                "rerank_top_k",
                "use_reranker",
                "score_threshold",
                "search_mode",
                "rrf_k",
            )
            if k in raw_body
        }
        exec_params = normalize_query_execution_params(
            collection_query=collection_query,
            request_overrides=overrides,
        )

        from kb.retrieval import KbRetrievalService

        search_result = KbRetrievalService.search(
            collection_name=collection_name,
            query=query.strip(),
            query_execution_params=exec_params,
            filters=nb.filters,
            vector_dimension=vd,
        )

        results = [
            SearchResult(
                id=h.id,
                score=h.score,
                content=h.content,
                file_name=h.file_name,
                search_mode=h.search_mode,
                header_path=h.header_path,
                recall_score=h.recall_score,
                rerank_score=h.rerank_score,
            ).model_dump()
            for h in search_result.hits
        ]
        timing = SearchTiming(
            prepare_ms=search_result.timing.prepare_ms,
            recall_ms=search_result.timing.recall_ms,
            parse_ms=search_result.timing.parse_ms,
            rerank_ms=search_result.timing.rerank_ms,
            post_ms=search_result.timing.post_ms,
            total_ms=search_result.timing.total_ms,
            rerank_applied=search_result.timing.rerank_applied,
            recall_hits=search_result.timing.recall_hits,
            final_hits=search_result.timing.final_hits,
            search_mode=search_result.timing.search_mode,
        ).model_dump()
        payload = SearchCollectionResponse(results=results, timing=timing).model_dump()
        return ResponseUtil.success(msg="检索成功", data=payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.error(f"检索失败: {e}")
        raise HTTPException(status_code=500, detail=f"检索失败: {str(e)}") from e
