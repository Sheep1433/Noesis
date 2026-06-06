"""
知识库管理 API（集合、文档、分片、检索均仅依赖 Qdrant）
"""
import logging
import os
import tempfile
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from schemas.knowledge_base_schema import (
    CollectionInfo,
    CollectionDetail,
    DocumentInfo,
    ShardInfo,
    ShardDetail,
    KnowledgeBaseStatus,
    SearchResult,
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
from kb.chunk import (
    merge_query_execution_params,
    normalize_mysql_query_params,
)
from config.env import QdrantConfig
from utils.response_util import ResponseUtil


logger = logging.getLogger(__name__)
knowledge_base_router = APIRouter(prefix='/api/knowledge_base', tags=['知识库模块'])


def _global_query_defaults():
    """平台级检索默认（代码常量，不读 MySQL）。"""
    return normalize_mysql_query_params({})


@knowledge_base_router.get('/status', response_model=KnowledgeBaseStatus)
async def get_status():
    """获取向量库连接状态"""
    connected = is_qdrant_connected()
    client = get_qdrant_client()
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
    )


@knowledge_base_router.get('/collections', response_model=List[CollectionInfo])
async def get_collections():
    """获取所有 Collection 列表（数据来自 Qdrant）"""
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    collections = service.get_collections()

    return [
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


@knowledge_base_router.post('/collections', response_model=CreateCollectionResponse)
async def create_collection(request: CreateCollectionRequest):
    """创建 Collection（仅写入 Qdrant）"""
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

    return CreateCollectionResponse(
        success=True,
        message=result['message'],
        name=result['name'],
    )


@knowledge_base_router.delete('/collections/{collection_name}')
async def delete_collection(collection_name: str):
    """删除 Collection（仅操作 Qdrant）"""
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    result = service.delete_collection(collection_name)

    if not result['success']:
        raise HTTPException(status_code=400, detail=result['message'])

    return result


@knowledge_base_router.get('/collections/{collection_name}', response_model=CollectionDetail)
async def get_collection(collection_name: str):
    """获取指定 Collection 详情（数据来自 Qdrant）"""
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    collection = service.get_collection(collection_name)

    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection '{collection_name}' 不存在")

    return CollectionDetail(
        name=collection.get("name", collection_name),
        vector_dimension=int(collection.get("vector_dimension", 1024)),
        documents_count=collection.get("documents_count", 0),
        points_count=collection.get("points_count", 0),
        created_at=collection.get("created_at"),
        status=collection.get("status"),
    )


@knowledge_base_router.get('/collections/{collection_name}/documents', response_model=List[DocumentInfo])
async def get_documents(collection_name: str):
    """获取 Collection 下的文档列表"""
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    return service.get_collection_documents(collection_name)


@knowledge_base_router.get(
    '/collections/{collection_name}/documents/{file_name}/shards',
    response_model=List[ShardInfo],
)
async def get_shards(collection_name: str, file_name: str):
    """获取文档的分片列表"""
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    return service.get_document_shards(collection_name, file_name)


@knowledge_base_router.get(
    '/collections/{collection_name}/shards/{shard_id}',
    response_model=ShardDetail,
)
async def get_shard_detail(collection_name: str, shard_id: str):
    """获取分片详情"""
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    shard = service.get_shard_detail(collection_name, shard_id)

    if not shard:
        raise HTTPException(status_code=404, detail=f"分片 '{shard_id}' 不存在")

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
        chunk_index=shard.get("chunk_index"),
    )


@knowledge_base_router.delete(
    '/collections/{collection_name}/documents/{file_name}',
    response_model=DeleteResponse,
)
async def delete_document(collection_name: str, file_name: str):
    """删除文档及其所有分片"""
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    service = QdrantService()
    result = service.delete_document(collection_name, file_name)

    if not result['success']:
        raise HTTPException(status_code=500, detail=result['message'])

    return DeleteResponse(
        success=result['success'],
        message=result['message'],
        deleted_count=result['deleted_count'],
    )


@knowledge_base_router.post(
    '/collections/{collection_name}/upload',
    response_model=UploadResponse,
)
async def upload_document(
    collection_name: str,
    file: UploadFile = File(...),
):
    """上传文档：DocumentParser 统一解析分块后写入 Qdrant。"""
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    suffix = os.path.splitext(file.filename)[1] if file.filename else '.tmp'
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name

    try:
        service = QdrantService()
        collection_info = service.get_collection(collection_name)
        if not collection_info:
            raise HTTPException(status_code=404, detail=f"Collection '{collection_name}' 不存在")

        vector_dim = int(collection_info.get('vector_dimension', 1024))

        result = service.upload_document(
            collection_name=collection_name,
            file_name=file.filename or 'unknown',
            file_path=tmp_path,
            vector_dim=vector_dim,
        )

        if not result['success']:
            code = int(result.get('code') or 500)
            if code == 409:
                raise HTTPException(status_code=409, detail=result['message'])
            raise HTTPException(status_code=500, detail=result['message'])

        return UploadResponse(
            success=True,
            message=result['message'],
            file_name=file.filename,
            shards_created=result['shards_created'],
            extracted_markdown=result.get('extracted_markdown'),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传文档失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}") from e
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@knowledge_base_router.post('/collections/{collection_name}/search')
async def search_collection(
    collection_name: str,
    body: SearchCollectionBody,
):
    """
    知识库检索：支持 vector / bm25 / hybrid（RRF）与可选 filters。
    集合由路径 collection_name 指定；未传的 limit/score_threshold 使用平台代码默认值。
    """
    if not is_qdrant_connected():
        raise HTTPException(status_code=503, detail="向量库未连接")

    nb = body
    query = nb.query
    if not query or not str(query).strip():
        raise HTTPException(status_code=400, detail="查询文本不能为空")

    try:
        service = QdrantService()
        col_info = service.get_collection(collection_name)
        if not col_info:
            raise HTTPException(status_code=404, detail=f"Collection '{collection_name}' 不存在")
        vd = int(col_info.get("vector_dimension", 1024))

        raw_body = nb.model_dump(exclude_unset=True)
        overrides = {
            k: raw_body[k]
            for k in ("limit", "score_threshold", "search_mode", "rrf_k")
            if k in raw_body
        }
        exec_params = merge_query_execution_params(
            persisted=_global_query_defaults(),
            request_overrides=overrides,
        )
        lim = exec_params.get("limit")
        try:
            lim_i = max(1, int(lim if lim is not None else 10))
        except (TypeError, ValueError):
            lim_i = 10
        st = exec_params.get("score_threshold")
        score_threshold = float(st) if isinstance(st, (float, int)) else None

        search_mode = str(exec_params.get("search_mode") or nb.search_mode or "vector")
        rrf_k_raw = exec_params.get("rrf_k")
        if rrf_k_raw is None and nb.rrf_k is not None:
            rrf_k_raw = nb.rrf_k
        try:
            rrf_k = max(1, int(rrf_k_raw if rrf_k_raw is not None else 60))
        except (TypeError, ValueError):
            rrf_k = 60

        from kb.retrieval import KbRetrievalService

        hits = KbRetrievalService.search(
            collection_name=collection_name,
            query=query.strip(),
            search_mode=search_mode,
            limit=lim_i,
            score_threshold=score_threshold,
            filters=nb.filters,
            rrf_k=rrf_k,
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
            ).model_dump()
            for h in hits
        ]
        return ResponseUtil.success(msg="检索成功", data=results)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.error(f"检索失败: {e}")
        raise HTTPException(status_code=500, detail=f"检索失败: {str(e)}") from e
