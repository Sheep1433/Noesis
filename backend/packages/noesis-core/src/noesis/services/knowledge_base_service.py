"""
知识库领域编排：Qdrant 向量数据 + PostgreSQL 集合配置。

service 层不碰 HTTP（不抛 HTTPException、不用 ResponseUtil）。
抛 domain exception（NotFoundException/QdrantNotConnectedError 等），
api 层 catch 后映射 HTTP。返回 dict（VO 构造由 service 完成，api 只包装）。
"""
import json
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.knowledge_base_schema import (
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
    ChunkSummaryPage,
    ShardPageQuery,
)
from noesis.knowledge.implementations.qdrant import QdrantService
from noesis.knowledge.runtime import knowledge_base
from noesis.knowledge.base import KBNotFoundError, QdrantNotConnectedError
from noesis.errors.exceptions import NotFoundException, ServiceException
from noesis.services.kb_collection_config_service import KbCollectionConfigService
from noesis.knowledge.chunking import (
    normalize_query_execution_params,
    resolve_effective_processing_params,
)
from noesis.knowledge.parser.staging import sanitize_kb_filename, write_staging
from noesis.config.env import QdrantConfig
from noesis.runtime.logging import logger




async def _require_collection_in_qdrant(service: QdrantService, collection_name: str) -> dict:
    col_info = service.get_collection(collection_name)
    if not col_info:
        raise NotFoundException(message=f"Collection '{collection_name}' 不存在")
    return col_info


async def _require_collection_config(
    db: AsyncSession,
    service: QdrantService,
    collection_name: str,
) -> dict:
    await _require_collection_in_qdrant(service, collection_name)
    cfg = await KbCollectionConfigService.get_config(db, collection_name)
    if cfg is None:
        raise NotFoundException(message=f"Collection '{collection_name}' 配置不存在")
    return cfg


def _ensure_connected() -> None:
    if not knowledge_base.connected:
        raise QdrantNotConnectedError("向量库未连接")


async def get_status(current_user: CurrentUser) -> Dict[str, Any]:
    """获取向量库连接状态"""
    _ = current_user
    connected = knowledge_base.connected
    client = knowledge_base.client
    collections_count = 0

    if connected and client:
        try:
            collections_count = len(client.get_collections().model_dump().get('collections', []))
        except Exception as e:
            logger.error(f"获取 collections 数量失败: {e}")

    return KnowledgeBaseStatus(
        connected=connected,
        host=QdrantConfig.qdrant_host,
        port=QdrantConfig.qdrant_port,
        collections_count=collections_count,
    ).model_dump()


async def get_collections(current_user: CurrentUser) -> list:
    """获取所有 Collection 列表（数据来自 Qdrant）"""
    _ = current_user
    _ensure_connected()

    service = knowledge_base.service()
    collections = service.get_collections()

    items = [
        CollectionInfo(
            name=c.get("name", ""),
            vector_dimension=c.get("vector_dimension", 1024),
            documents_count=c.get("documents_count", 0),
            points_count=c.get("points_count", 0),
            created_at=c.get("created_at"),
        ).model_dump()
        for c in collections
        if c.get("name")
    ]
    return items


async def create_collection(
    request: CreateCollectionRequest,
    current_user: CurrentUser,
    db: AsyncSession,
) -> Dict[str, Any]:
    """创建 Collection（Qdrant + PostgreSQL 默认配置）"""
    _ = current_user
    _ensure_connected()

    service = knowledge_base.service()
    result = service.create_collection(
        collection_name=request.name,
        vector_dimension=request.vector_dimension,
        description=request.description,
    )

    if not result['success']:
        raise ServiceException(message=result['message'])

    await KbCollectionConfigService.create_default(db, request.name)
    await db.commit()

    return CreateCollectionResponse(
        success=True,
        message=result['message'],
        name=result['name'],
    ).model_dump()


async def delete_collection(
    collection_name: str,
    current_user: CurrentUser,
    db: AsyncSession,
) -> Dict[str, Any]:
    """删除 Collection（Qdrant + PostgreSQL 配置）"""
    _ = current_user
    _ensure_connected()

    service = knowledge_base.service()
    result = service.delete_collection(collection_name)

    if not result['success']:
        raise ServiceException(message=result['message'])

    await KbCollectionConfigService.delete_config(db, collection_name)
    await db.commit()

    return result


async def get_collection(
    collection_name: str,
    current_user: CurrentUser,
) -> Dict[str, Any]:
    """获取指定 Collection 详情（数据来自 Qdrant）"""
    _ = current_user
    _ensure_connected()

    service = knowledge_base.service()
    collection = service.get_collection(collection_name)

    if not collection:
        raise NotFoundException(message=f"Collection '{collection_name}' 不存在")

    return CollectionDetail(
        name=collection.get("name", collection_name),
        vector_dimension=int(collection.get("vector_dimension", 1024)),
        documents_count=collection.get("documents_count", 0),
        points_count=collection.get("points_count", 0),
        created_at=collection.get("created_at"),
        status=collection.get("status"),
    ).model_dump()


async def get_collection_config(
    collection_name: str,
    current_user: CurrentUser,
    db: AsyncSession,
) -> Dict[str, Any]:
    """读取集合 PostgreSQL 配置"""
    _ = current_user
    service = knowledge_base.service()
    cfg = await _require_collection_config(db, service, collection_name)
    return CollectionConfigResponse(**cfg).model_dump()


async def patch_collection_config(
    collection_name: str,
    body: PatchCollectionConfigRequest,
    current_user: CurrentUser,
    db: AsyncSession,
) -> Dict[str, Any]:
    """部分更新集合配置（deep-merge）"""
    _ = current_user
    service = knowledge_base.service()
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
        raise NotFoundException(message=f"Collection '{collection_name}' 配置不存在")
    return CollectionConfigResponse(**updated).model_dump()


async def get_documents(
    collection_name: str,
    current_user: CurrentUser,
) -> list:
    """获取 Collection 下的文档列表"""
    _ = current_user
    _ensure_connected()

    service = knowledge_base.service()
    return service.get_collection_documents(collection_name)


async def get_shards(
    collection_name: str,
    file_name: str,
    query: ShardPageQuery,
    current_user: CurrentUser,
) -> Dict[str, Any]:
    """获取文档的分片分页摘要。"""
    _ = current_user
    _ensure_connected()

    service = knowledge_base.service()
    try:
        page = service.get_document_shards_page(collection_name, file_name, query)
    except KBNotFoundError as exc:
        raise NotFoundException(message=str(exc)) from exc
    return ChunkSummaryPage(**page).model_dump()


async def get_shard_detail(
    collection_name: str,
    shard_id: str,
    current_user: CurrentUser,
) -> Dict[str, Any]:
    """获取分片详情"""
    _ = current_user
    _ensure_connected()

    service = knowledge_base.service()
    shard = service.get_shard_detail(collection_name, shard_id)

    if not shard:
        raise NotFoundException(message=f"分片 '{shard_id}' 不存在")

    return ShardDetail(
        id=str(shard["id"]),
        content=shard["content"],
        char_length=int(shard.get("char_length", 0)),
        vector_dimension=int(shard.get("vector_dimension", 0)),
        created_at=shard.get("created_at"),
        header_path=shard.get("header_path"),
        Header_1=shard.get("Header_1"),
        Header_2=shard.get("Header_2"),
        Header_3=shard.get("Header_3"),
        Header_4=shard.get("Header_4"),
        chunk_index=shard.get("chunk_index"),
        locator=shard.get("locator"),
        element_type=shard.get("element_type"),
        token_count=shard.get("token_count"),
        file_name=shard.get("file_name"),
        file_hash=shard.get("file_hash"),
        content_hash=shard.get("content_hash"),
        document_id=shard.get("document_id"),
        document_version_id=shard.get("document_version_id"),
        segment_id=shard.get("segment_id"),
        source=shard.get("source"),
        raw_text=shard.get("raw_text"),
        clean_text=shard.get("clean_text"),
        raw_metadata=shard.get("raw_metadata") or {},
        effective_processing_params=shard.get("effective_processing_params"),
    ).model_dump()


async def delete_document(
    collection_name: str,
    file_name: str,
    current_user: CurrentUser,
) -> Dict[str, Any]:
    """删除文档及其所有分片"""
    _ = current_user
    _ensure_connected()

    service = knowledge_base.service()
    result = service.delete_document(collection_name, file_name)

    if not result['success']:
        raise ServiceException(message=result['message'])

    return DeleteResponse(
        success=result['success'],
        message=result['message'],
        deleted_count=result['deleted_count'],
    ).model_dump()


async def upload_document(
    collection_name: str,
    *,
    file_name: str,
    content: bytes,
    processing_params: Optional[str],
    current_user: CurrentUser,
    db: AsyncSession,
) -> Dict[str, Any]:
    """上传文档：解析分块后写入 Qdrant。"""
    _ = current_user
    _ensure_connected()

    request_once = None
    if processing_params and processing_params.strip():
        try:
            request_once = json.loads(processing_params)
        except json.JSONDecodeError as exc:
            raise ServiceException(message=f"processing_params JSON 无效: {exc}") from exc

    original_name = sanitize_kb_filename(file_name or "unknown")
    staging_path, _file_hash = write_staging(collection_name, content, original_name)

    try:
        service = knowledge_base.service()
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
            raise ServiceException(message=result['message'])

        return UploadResponse(
            success=True,
            message=result['message'],
            file_name=original_name,
            shards_created=result['shards_created'],
            extracted_markdown=result.get('extracted_markdown'),
        ).model_dump()
    except (NotFoundException, ServiceException, QdrantNotConnectedError):
        raise
    except Exception as e:
        logger.exception("上传文档服务异常 collection={} file={}", collection_name, original_name)
        raise ServiceException(message="上传失败，请稍后重试") from e
    finally:
        if staging_path.exists():
            staging_path.unlink()


async def search_collection(
    collection_name: str,
    body: SearchCollectionBody,
    current_user: CurrentUser,
    db: AsyncSession,
) -> Dict[str, Any]:
    """知识库检索：hybrid 默认 + recall → rerank → final_top_k。"""
    _ = current_user
    _ensure_connected()

    nb = body
    query = nb.query
    if not query or not str(query).strip():
        raise ServiceException(message="查询文本不能为空")

    service = knowledge_base.service()
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

    from noesis.knowledge.retrieval import KbRetrievalService

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
    return SearchCollectionResponse(results=results, timing=timing).model_dump()
