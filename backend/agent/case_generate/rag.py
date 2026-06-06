"""
测试用例生成 — 知识库引用约定与场景级 RAG 召回。

- 阶段 A：file_dict 哨兵 + requirement collection 解析（供 case_coordinator 拉整篇文档）
- 阶段 B：按场景三路 hybrid 召回并拼接 prompt 上下文
  1. 当前需求文档（requirement_docs + file_name 过滤）
  2. 历史相关需求（同 collection，排除当前文档；可配置开关）
  3. 历史测试用例（test_case_docs）
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from config.env import QdrantConfig
from kb.retrieval import KbSearchHit
from utils.langfuse_tracing import hits_to_langfuse_payload, langfuse_retrieval_observation
from utils.log_util import logger

# file_dict 值为该哨兵时，以 key 作为 file_name 从 requirement_docs 拉取整篇
TEST_CASE_KB_FILE_DICT_REF = "__FROM_KB__"

CHANNEL_CURRENT_REQUIREMENT = "current_requirement"
CHANNEL_HISTORICAL_REQUIREMENT = "historical_requirements"
CHANNEL_HISTORICAL_TEST_CASES = "historical_test_cases"

DEFAULT_TOP_K = 3
_DEFAULT_TIMEOUT = 10.0
_LANGFUSE_CONTEXT_PREVIEW = 2000


def requirement_collection_name() -> str:
    """解析需求文档 Qdrant collection 名称。"""
    override = (QdrantConfig.test_case_upload_collection or "").strip()
    if override:
        return override
    return (QdrantConfig.requirement_docs_collection or "").strip() or "requirement_docs"


def extract_source_file_names(file_list: Optional[Dict[str, Any]]) -> List[str]:
    """从 file_dict 提取当前会话关联的需求文档 file_name 列表。"""
    if not file_list:
        return []
    names: List[str] = []
    for name, val in file_list.items():
        file_name = str(name).strip()
        if not file_name:
            continue
        s = str(val).strip() if val is not None else ""
        if s == TEST_CASE_KB_FILE_DICT_REF or len(s) > 800:
            names.append(file_name)
    return names


def _current_requirement_filters(source_file_names: List[str]) -> Optional[Dict[str, Any]]:
    if not source_file_names:
        return None
    if len(source_file_names) == 1:
        return {"file_name": source_file_names[0]}
    return {"file_name_in": list(source_file_names)}


def _historical_requirement_filters(source_file_names: List[str]) -> Optional[Dict[str, Any]]:
    if source_file_names:
        return {"exclude_file_names": list(source_file_names)}
    return None


class _HybridRetriever:
    """带超时的 hybrid 检索封装，委托 KbRetrievalService。"""

    def __init__(self, collection_name: str, *, timeout: float = _DEFAULT_TIMEOUT):
        self.collection_name = collection_name
        self.timeout = timeout

    async def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_TOP_K,
        filters: Optional[Dict[str, Any]] = None,
        vector_dimension: int = 1024,
    ) -> List[KbSearchHit]:
        if not query or not str(query).strip():
            return []

        def _run() -> List[KbSearchHit]:
            from kb.retrieval import KbRetrievalService

            return KbRetrievalService.search(
                collection_name=self.collection_name,
                query=query.strip(),
                search_mode="hybrid",
                limit=limit,
                filters=filters,
                vector_dimension=vector_dimension,
            )

        try:
            return await asyncio.wait_for(asyncio.to_thread(_run), timeout=self.timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[CaseRAG] hybrid search 超时 collection={self.collection_name}")
            return []
        except Exception as e:
            logger.exception(f"[CaseRAG] hybrid search 异常 collection={self.collection_name}: {e}")
            return []


def _build_query_text(
    scene: Dict[str, Any],
    adopted_point_names: Optional[List[str]] = None,
) -> str:
    parts: List[str] = []
    for key in ("scene_name", "scene_description"):
        val = str(scene.get(key) or "").strip()
        if val:
            parts.append(val)
    if adopted_point_names:
        for name in adopted_point_names:
            n = str(name or "").strip()
            if n:
                parts.append(n)
    return " ".join(parts)


def _hits_to_section(title: str, hits: List[KbSearchHit], collection: str) -> str:
    if not hits:
        return ""
    bodies = [h.content for h in hits if h.content]
    if not bodies:
        return ""
    return f"## {title}（hybrid，collection={collection}）\n" + "\n\n".join(bodies)


def _channel_trace(hits: List[KbSearchHit]) -> Dict[str, Any]:
    return {"hit_ids": [h.id for h in hits if h.id]}


async def _search_channel(
    retriever: _HybridRetriever,
    *,
    query: str,
    channel_key: str,
    section_title: str,
    limit: int = DEFAULT_TOP_K,
    filters: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[KbSearchHit], Dict[str, Any]]:
    """单通道 hybrid 召回，并在 Langfuse 中记录 retrieval span。"""
    collection = retriever.collection_name
    with langfuse_retrieval_observation(
        name=f"rag/{collection}/{channel_key}",
        input_data={
            "query": query,
            "collection": collection,
            "channel": channel_key,
            "limit": limit,
            "filters": filters,
        },
    ) as span:
        hits = await retriever.search(query, limit=limit, filters=filters)
        section = _hits_to_section(section_title, hits, collection)
        trace = _channel_trace(hits)
        if span is not None:
            span.update(
                output={
                    "hit_count": len(hits),
                    "hits": hits_to_langfuse_payload(hits),
                    "section_length": len(section),
                }
            )
    return section, hits, trace


async def _empty_channel() -> Tuple[str, List[KbSearchHit], Dict[str, Any]]:
    return "", [], {"hit_ids": []}


async def _build_scene_rag_context_impl(
    scene: Dict[str, Any],
    *,
    adopted_point_names: Optional[List[str]] = None,
    source_file_names: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    scene_name = str(scene.get("scene_name") or "").strip()
    query = _build_query_text(scene, adopted_point_names)
    source_names = [str(x).strip() for x in (source_file_names or []) if str(x).strip()]

    req_coll = requirement_collection_name()
    tc_coll = (QdrantConfig.test_case_docs_collection or "").strip() or "test_case_docs"
    req_retriever = _HybridRetriever(collection_name=req_coll)
    tc_retriever = _HybridRetriever(collection_name=tc_coll)

    current_filters = _current_requirement_filters(source_names)
    historical_enabled = bool(QdrantConfig.case_rag_historical_requirements_enabled)
    historical_filters = (
        _historical_requirement_filters(source_names) if historical_enabled else None
    )

    current_coro = (
        _search_channel(
            req_retriever,
            query=query,
            channel_key=CHANNEL_CURRENT_REQUIREMENT,
            section_title="当前需求文档片段",
            filters=current_filters,
        )
        if current_filters
        else _empty_channel()
    )
    historical_coro = (
        _search_channel(
            req_retriever,
            query=query,
            channel_key=CHANNEL_HISTORICAL_REQUIREMENT,
            section_title="历史相关需求片段",
            filters=historical_filters,
        )
        if historical_filters is not None
        else _empty_channel()
    )
    test_cases_coro = _search_channel(
        tc_retriever,
        query=query,
        channel_key=CHANNEL_HISTORICAL_TEST_CASES,
        section_title="历史测试用例参考",
    )

    (
        (current_section, current_hits, current_trace),
        (historical_section, historical_hits, historical_trace),
        (tc_section, tc_hits, tc_trace),
    ) = await asyncio.gather(current_coro, historical_coro, test_cases_coro)

    sections: List[str] = []
    channels: Dict[str, Dict[str, Any]] = {}

    if current_filters:
        if current_section:
            sections.append(current_section)
        channels[CHANNEL_CURRENT_REQUIREMENT] = current_trace
        logger.info(
            f"[CaseRAG] scene={scene_name} channel={CHANNEL_CURRENT_REQUIREMENT} "
            f"hits={len(current_hits)} collection={req_coll} files={source_names}"
        )

    if historical_section:
        sections.append(historical_section)
    if historical_filters is not None:
        channels[CHANNEL_HISTORICAL_REQUIREMENT] = historical_trace
        logger.info(
            f"[CaseRAG] scene={scene_name} channel={CHANNEL_HISTORICAL_REQUIREMENT} "
            f"hits={len(historical_hits)} collection={req_coll}"
        )

    if tc_section:
        sections.append(tc_section)
    channels[CHANNEL_HISTORICAL_TEST_CASES] = tc_trace
    logger.info(
        f"[CaseRAG] scene={scene_name} channel={CHANNEL_HISTORICAL_TEST_CASES} hits={len(tc_hits)}"
    )

    context = "\n\n".join(sections)
    trace_entry = {
        "scene_name": scene_name,
        "channels": channels,
    }
    return context, trace_entry


async def build_scene_rag_context(
    scene: Dict[str, Any],
    *,
    adopted_point_names: Optional[List[str]] = None,
    source_file_names: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    对单个场景执行三路 hybrid 召回并拼接 Markdown 上下文。

    Returns:
        (scene_rag_context, trace_entry) — trace_entry 含 scene_name 与各 channel hit_ids。
    """
    scene_name = str(scene.get("scene_name") or "").strip()
    query = _build_query_text(scene, adopted_point_names)

    with langfuse_retrieval_observation(
        name="case_rag_retrieval",
        input_data={
            "scene_name": scene_name,
            "query": query,
            "adopted_point_names": adopted_point_names or [],
            "source_file_names": source_file_names or [],
        },
    ) as root_span:
        context, trace_entry = await _build_scene_rag_context_impl(
            scene,
            adopted_point_names=adopted_point_names,
            source_file_names=source_file_names,
        )
        if root_span is not None:
            preview = context[:_LANGFUSE_CONTEXT_PREVIEW]
            if len(context) > _LANGFUSE_CONTEXT_PREVIEW:
                preview += "…"
            root_span.update(
                output={
                    "context_length": len(context),
                    "context_preview": preview,
                    "channels": trace_entry.get("channels") or {},
                }
            )
        return context, trace_entry
