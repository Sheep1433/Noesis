"""知识库检索 Tool（COMMON_QA）：可选范围、并行 hybrid 检索、整篇拉取。"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from kb.chunk import normalize_query_execution_params
from kb.retrieval.service import KbRetrievalService, KbSearchHit
from services.kb_collection_config_service import KbCollectionConfigService
from services.qdrant_service import QdrantService, is_qdrant_connected
from common.logging import logger

_MAX_DOCUMENT_CHARS = 80_000
_MAX_PARALLEL_COLLECTIONS = 8


class KbSearchInput(BaseModel):
    query: str = Field(description="检索关键词或用户问题的改写")
    collection_names: Optional[List[str]] = Field(
        default=None,
        description="限定检索的知识库 Collection 名称列表；省略则使用会话默认范围或全部可用库",
    )
    limit: int = Field(default=10, ge=1, le=20, description="全局返回片段数量上限")


class KbGetDocumentInput(BaseModel):
    collection_name: str = Field(description="知识库 Collection 名称")
    file_name: str = Field(description="文档 file_name（与检索结果中的 file_name 一致）")


def list_qdrant_collection_names() -> List[str]:
    """列出当前可检索的 Collection（已连接且有点数）。"""
    if not is_qdrant_connected():
        return []
    names: List[str] = []
    for col in QdrantService().get_collections():
        name = (col.get("name") or "").strip()
        if not name:
            continue
        if int(col.get("points_count") or 0) <= 0:
            continue
        names.append(name)
    return names


def _normalize_name_list(raw: Optional[List[str]]) -> List[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def resolve_search_collections(
    *,
    collection_names: Optional[List[str]] = None,
    default_collection_names: Optional[List[str]] = None,
) -> Tuple[List[str], Optional[str]]:
    """
    解析检索范围：工具入参 > 会话默认 > 全部可用库。
    返回 (collections, error_json)；error_json 非空时 collections 为空。
    """
    available = list_qdrant_collection_names()
    if not available:
        return [], json.dumps(
            {"hits": [], "message": "当前无可用知识库 Collection"},
            ensure_ascii=False,
        )

    available_set = set(available)
    requested = _normalize_name_list(collection_names)
    if requested:
        valid = [n for n in requested if n in available_set]
        invalid = [n for n in requested if n not in available_set]
        if not valid:
            return [], json.dumps(
                {
                    "hits": [],
                    "message": "指定的知识库均不可用或不存在",
                    "invalid_collections": invalid,
                    "available_collections": available,
                },
                ensure_ascii=False,
            )
        if invalid:
            logger.warning(f"search_knowledge_base 忽略无效 collection: {invalid}")
        return valid, None

    scoped_default = _normalize_name_list(default_collection_names)
    if scoped_default:
        valid = [n for n in scoped_default if n in available_set]
        if not valid:
            return [], json.dumps(
                {
                    "hits": [],
                    "message": "会话选定的知识库均不可用，请在界面重新选择或调用 list_knowledge_bases",
                    "requested_collections": scoped_default,
                    "available_collections": available,
                },
                ensure_ascii=False,
            )
        return valid, None

    return available, None


def _format_hits(scored: List[Tuple[str, KbSearchHit]]) -> str:
    if not scored:
        return json.dumps(
            {"hits": [], "message": "未检索到相关片段"},
            ensure_ascii=False,
        )
    rows = []
    for i, (collection_name, hit) in enumerate(scored, 1):
        rows.append(
            {
                "rank": i,
                "collection_name": collection_name,
                "file_name": hit.file_name,
                "score": round(hit.score, 4),
                "recall_score": round(hit.recall_score, 4) if hit.recall_score is not None else None,
                "rerank_score": round(hit.rerank_score, 4) if hit.rerank_score is not None else None,
                "search_mode": hit.search_mode,
                "header_path": hit.header_path,
                "content": hit.content,
            }
        )
    return json.dumps({"hits": rows}, ensure_ascii=False)


def _search_one_collection(
    name: str,
    query: str,
    *,
    global_limit: int,
) -> List[Tuple[str, KbSearchHit]]:
    svc = QdrantService()
    col = svc.get_collection(name)
    if not col:
        return []
    vd = int(col.get("vector_dimension") or 1024)
    collection_query = KbCollectionConfigService.load_query_params_sync(name)
    exec_params = normalize_query_execution_params(
        collection_query=collection_query,
        request_overrides={"final_top_k": global_limit},
    )
    hits = KbRetrievalService.search(
        collection_name=name,
        query=query,
        query_execution_params=exec_params,
        vector_dimension=vd,
    )
    return [(name, hit) for hit in hits.hits]


def search_knowledge_bases_all(
    query: str,
    limit: int = 10,
    collection_names: Optional[List[str]] = None,
    *,
    default_collection_names: Optional[List[str]] = None,
) -> str:
    """在指定或默认范围内的知识库 Collection 并行 hybrid 检索，全局 Top-K。"""
    t_total = time.perf_counter()
    if not is_qdrant_connected():
        return json.dumps(
            {"error": "向量库未连接，无法检索"},
            ensure_ascii=False,
        )

    t_resolve = time.perf_counter()
    collections, err = resolve_search_collections(
        collection_names=collection_names,
        default_collection_names=default_collection_names,
    )
    resolve_ms = (time.perf_counter() - t_resolve) * 1000
    if err:
        logger.info(
            "[KbSearchTool] search_knowledge_base "
            f"resolve_ms={resolve_ms:.1f} total_ms={(time.perf_counter() - t_total) * 1000:.1f} "
            "result=scope_error"
        )
        return err

    global_limit = max(1, min(20, int(limit or 10)))
    merged: List[Tuple[str, KbSearchHit]] = []
    workers = min(len(collections), _MAX_PARALLEL_COLLECTIONS)

    t_parallel = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _search_one_collection,
                name,
                query,
                global_limit=global_limit,
            ): name
            for name in collections
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                merged.extend(fut.result())
            except Exception as e:
                logger.warning(f"search_knowledge_base 跳过 collection={name}: {e}")
    parallel_ms = (time.perf_counter() - t_parallel) * 1000

    t_merge = time.perf_counter()
    merged.sort(key=lambda item: item[1].score, reverse=True)
    top = merged[:global_limit]
    merge_ms = (time.perf_counter() - t_merge) * 1000
    total_ms = (time.perf_counter() - t_total) * 1000

    logger.info(
        "[KbSearchTool] search_knowledge_base "
        f"resolve_ms={resolve_ms:.1f} parallel_ms={parallel_ms:.1f} "
        f"merge_ms={merge_ms:.1f} collections={len(collections)} "
        f"merged_hits={len(merged)} final_hits={len(top)} "
        f"query_len={len(query)} total_ms={total_ms:.1f}"
    )
    return _format_hits(top)


def list_knowledge_bases(
    *,
    default_collection_names: Optional[List[str]] = None,
) -> str:
    """列出企业内可检索的知识库 Collection 及文档规模。"""
    if not is_qdrant_connected():
        return json.dumps(
            {"error": "向量库未连接，无法列出知识库"},
            ensure_ascii=False,
        )

    scope = _normalize_name_list(default_collection_names)
    svc = QdrantService()
    rows = []
    for name in list_qdrant_collection_names():
        if scope and name not in scope:
            continue
        col = svc.get_collection(name)
        if not col:
            continue
        rows.append(
            {
                "collection_name": name,
                "documents_count": int(col.get("documents_count") or 0),
                "points_count": int(col.get("points_count") or 0),
            }
        )

    return json.dumps(
        {
            "collections": rows,
            "message": "未选择知识库时会检索全部可用库；search_knowledge_base 可传 collection_names 进一步限定",
        },
        ensure_ascii=False,
    )


def get_knowledge_document(
    collection_name: str,
    file_name: str,
    *,
    allowed_collection_names: Optional[List[str]] = None,
) -> str:
    """按 collection + file_name 拉取整篇文档正文（chunk 检索不够时补全上下文）。"""
    if not is_qdrant_connected():
        return json.dumps(
            {"error": "向量库未连接，无法读取文档"},
            ensure_ascii=False,
        )

    col_name = (collection_name or "").strip()
    doc_name = (file_name or "").strip()
    if not col_name or not doc_name:
        return json.dumps(
            {"error": "collection_name 与 file_name 均不能为空"},
            ensure_ascii=False,
        )

    allowed = _normalize_name_list(allowed_collection_names)
    if allowed and col_name not in allowed:
        return json.dumps(
            {
                "error": f"知识库 {col_name} 不在当前会话检索范围内",
                "allowed_collections": allowed,
            },
            ensure_ascii=False,
        )

    available = set(list_qdrant_collection_names())
    if col_name not in available:
        return json.dumps(
            {
                "error": f"知识库 {col_name} 不存在或无可检索文档",
                "available_collections": sorted(available),
            },
            ensure_ascii=False,
        )

    content = KbRetrievalService.fetch_full_document_by_file_name(col_name, doc_name)
    if not content.strip():
        return json.dumps(
            {
                "collection_name": col_name,
                "file_name": doc_name,
                "content": "",
                "message": "未找到该文档或文档为空",
            },
            ensure_ascii=False,
        )

    truncated = False
    if len(content) > _MAX_DOCUMENT_CHARS:
        content = content[:_MAX_DOCUMENT_CHARS]
        truncated = True

    return json.dumps(
        {
            "collection_name": col_name,
            "file_name": doc_name,
            "content": content,
            "truncated": truncated,
            "max_chars": _MAX_DOCUMENT_CHARS,
        },
        ensure_ascii=False,
    )


def build_kb_search_tools(
    *,
    default_collection_names: Optional[List[str]] = None,
) -> list:
    """构建知识库 Tool；无 Collection 或向量库未连接时不挂载。"""
    if not is_qdrant_connected() or not list_qdrant_collection_names():
        return []

    scope = _normalize_name_list(default_collection_names)

    def _list() -> str:
        return list_knowledge_bases(default_collection_names=scope or None)

    def _search(
        query: str,
        collection_names: Optional[List[str]] = None,
        limit: int = 10,
    ) -> str:
        return search_knowledge_bases_all(
            query,
            limit=limit,
            collection_names=collection_names,
            default_collection_names=scope or None,
        )

    def _get_document(collection_name: str, file_name: str) -> str:
        return get_knowledge_document(
            collection_name,
            file_name,
            allowed_collection_names=scope or None,
        )

    tools = [
        StructuredTool.from_function(
            func=_list,
            name="list_knowledge_bases",
            description=(
                "列出企业内可检索的知识库 Collection（名称、文档数、分片数）。"
                "用户问题涉及特定业务域且未明确库名时，可先调用以选定 search_knowledge_base 的 collection_names。"
            ),
        ),
        StructuredTool.from_function(
            func=_search,
            name="search_knowledge_base",
            description=(
                "在知识库中执行 hybrid 检索（向量 + BM25 融合，多库并行后全局排序）。"
                "回答需要事实或文档依据时必须先调用；可传 collection_names 限定范围（省略则用会话默认或全部库）。"
                "引用时注明 collection_name 与 file_name；片段不足时可调用 get_knowledge_document 拉整篇。"
            ),
            args_schema=KbSearchInput,
        ),
        StructuredTool.from_function(
            func=_get_document,
            name="get_knowledge_document",
            description=(
                "按 collection_name 与 file_name 读取整篇文档正文。"
                "当 search_knowledge_base 返回的片段不足以回答、需要完整章节或表格上下文时使用。"
            ),
            args_schema=KbGetDocumentInput,
        ),
    ]
    return tools
