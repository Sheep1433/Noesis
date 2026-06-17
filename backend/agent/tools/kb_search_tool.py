"""知识库 hybrid 检索 Tool（COMMON_QA）：默认跨全部 Collection 召回。"""

from __future__ import annotations

import json
from typing import List, Tuple

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from kb.chunk import DEFAULT_COLLECTION_QUERY, merge_query_execution_params
from kb.retrieval.service import KbRetrievalService, KbSearchHit
from services.qdrant_service import QdrantService, is_qdrant_connected
from common.logging import logger


class KbSearchInput(BaseModel):
    query: str = Field(description="检索关键词或用户问题的改写")
    limit: int = Field(default=10, ge=1, le=20, description="全局返回片段数量上限")


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
                "search_mode": hit.search_mode,
                "header_path": hit.header_path,
                "content": hit.content,
            }
        )
    return json.dumps({"hits": rows}, ensure_ascii=False)


def search_knowledge_bases_all(query: str, limit: int = 10) -> str:
    """跨全部知识库 Collection 做 hybrid 检索并按 score 全局取 Top-K。"""
    if not is_qdrant_connected():
        return json.dumps(
            {"error": "向量库未连接，无法检索"},
            ensure_ascii=False,
        )

    collections = list_qdrant_collection_names()
    if not collections:
        return json.dumps(
            {"hits": [], "message": "当前无可用知识库 Collection"},
            ensure_ascii=False,
        )

    exec_params = merge_query_execution_params(
        persisted=dict(DEFAULT_COLLECTION_QUERY),
        request_overrides={"limit": limit},
    )
    global_limit = max(1, min(20, int(exec_params.get("limit") or 10)))
    score_threshold = exec_params.get("score_threshold")

    per_collection = max(3, (global_limit + len(collections) - 1) // len(collections))

    merged: List[Tuple[str, KbSearchHit]] = []
    svc = QdrantService()

    for name in collections:
        try:
            col = svc.get_collection(name)
            if not col:
                continue
            vd = int(col.get("vector_dimension") or 1024)
            hits = KbRetrievalService.search(
                collection_name=name,
                query=query,
                search_mode="hybrid",
                limit=per_collection,
                score_threshold=score_threshold,
                vector_dimension=vd,
            )
            for hit in hits:
                merged.append((name, hit))
        except Exception as e:
            logger.warning(f"search_knowledge_base 跳过 collection={name}: {e}")

    merged.sort(key=lambda item: item[1].score, reverse=True)
    top = merged[:global_limit]

    logger.info(
        f"search_knowledge_base query_len={len(query)} "
        f"collections={len(collections)} hits={len(top)}"
    )
    return _format_hits(top)


def build_kb_search_tools() -> list:
    """构建跨库检索 Tool；无 Collection 或向量库未连接时不挂载。"""
    if not is_qdrant_connected() or not list_qdrant_collection_names():
        return []

    tool = StructuredTool.from_function(
        func=search_knowledge_bases_all,
        name="search_knowledge_base",
        description=(
            "在企业全部知识库中执行 hybrid 检索（向量 + BM25 融合，跨 Collection 合并排序）。"
            "回答需要事实或文档依据时必须先调用本工具；引用时注明 collection_name 与 file_name。"
        ),
        args_schema=KbSearchInput,
    )
    return [tool]
