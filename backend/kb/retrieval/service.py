"""知识库统一检索门面。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

from langchain_core.documents import Document

if TYPE_CHECKING:
    from kb.retrieval.store import Retrieval

from config.env import QdrantConfig
from kb.chunk.params import normalize_query_execution_params
from kb.rerank import is_rerank_available, rerank_documents
from kb.retrieval.filters import document_matches_post_filter, split_search_filters
from services.qdrant_service import get_qdrant_client, is_qdrant_connected
from common.logging import logger

_VALID_MODES = frozenset({"vector", "bm25", "hybrid"})

_retrieval_cache: Dict[str, "Retrieval"] = {}


@dataclass
class KbSearchHit:
    id: str
    score: float
    content: str
    file_name: str
    search_mode: str
    header_path: Optional[str] = None
    recall_score: Optional[float] = None
    rerank_score: Optional[float] = None


class KbRetrievalService:
    """集合内检索：recall → rerank → score_threshold → final_top_k。"""

    @classmethod
    def qdrant_url(cls) -> str:
        return f"http://{QdrantConfig.qdrant_host}:{QdrantConfig.qdrant_port}"

    @classmethod
    def _get_retrieval(cls, collection_name: str, vector_dimension: int) -> "Retrieval":
        from kb.retrieval.store import create_retrieval_system

        key = f"{collection_name}:{vector_dimension}"
        cached = _retrieval_cache.get(key)
        if cached is not None:
            return cached
        retrieval = create_retrieval_system(
            url=cls.qdrant_url(),
            collection_name=collection_name,
            vector_size=vector_dimension,
            auto_load_documents=True,
        )
        _retrieval_cache[key] = retrieval
        return retrieval

    @classmethod
    def invalidate_cache(cls, collection_name: Optional[str] = None) -> None:
        if collection_name is None:
            _retrieval_cache.clear()
            return
        keys = [k for k in _retrieval_cache if k.startswith(f"{collection_name}:")]
        for k in keys:
            del _retrieval_cache[k]

    @classmethod
    def search(
        cls,
        *,
        collection_name: str,
        query: str,
        query_execution_params: Optional[Mapping[str, Any]] = None,
        collection_query_defaults: Optional[Mapping[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        vector_dimension: int = 1024,
        # legacy kwargs（测试与旧调用方）
        search_mode: Optional[str] = None,
        limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        rrf_k: Optional[int] = None,
        use_reranker: Optional[bool] = None,
        recall_top_k: Optional[int] = None,
        final_top_k: Optional[int] = None,
    ) -> List[KbSearchHit]:
        if not is_qdrant_connected():
            raise RuntimeError("向量库未连接")

        legacy_overrides: Dict[str, Any] = {}
        if search_mode is not None:
            legacy_overrides["search_mode"] = search_mode
        if limit is not None:
            legacy_overrides["limit"] = limit
        if final_top_k is not None:
            legacy_overrides["final_top_k"] = final_top_k
        if score_threshold is not None:
            legacy_overrides["score_threshold"] = score_threshold
        if rrf_k is not None:
            legacy_overrides["rrf_k"] = rrf_k
        if use_reranker is not None:
            legacy_overrides["use_reranker"] = use_reranker
        if recall_top_k is not None:
            legacy_overrides["recall_top_k"] = recall_top_k
        if query_execution_params:
            legacy_overrides.update(
                {k: v for k, v in dict(query_execution_params).items() if v is not None}
            )

        params = normalize_query_execution_params(
            collection_query=collection_query_defaults,
            request_overrides=legacy_overrides or None,
        )

        mode = str(params.get("search_mode") or "hybrid").strip().lower()
        if mode not in _VALID_MODES:
            raise ValueError(f"不支持的 search_mode: {mode}")

        try:
            recall_k = max(1, int(params.get("recall_top_k") or 50))
        except (TypeError, ValueError):
            recall_k = 50
        try:
            final_k = max(1, int(params.get("final_top_k") or 10))
        except (TypeError, ValueError):
            final_k = 10
        try:
            rrf_k_i = max(1, int(params.get("rrf_k") or 60))
        except (TypeError, ValueError):
            rrf_k_i = 60

        st_raw = params.get("score_threshold")
        threshold: Optional[float] = None
        if isinstance(st_raw, (int, float)):
            threshold = float(st_raw)

        use_rerank = bool(params.get("use_reranker", True))

        qdrant_filter, post_filter = split_search_filters(filters)
        retrieval = cls._get_retrieval(collection_name, vector_dimension)

        recall_limit = recall_k
        if post_filter:
            recall_limit = max(recall_k, recall_k * 3)

        if mode == "vector":
            scored = retrieval.vector_search(
                query,
                k=recall_limit,
                score_threshold=None,
                metadata_filter=qdrant_filter,
            )
        elif mode == "bm25":
            scored = retrieval.bm25_search_with_scores(query, k=recall_limit)
        else:
            scored = retrieval.hybrid_search_with_scores(
                query,
                k=recall_limit,
                rrf_k=rrf_k_i,
                metadata_filter=qdrant_filter,
            )

        hits = cls._hits_from_scored(
            scored, mode, post_filter, recall_limit, qdrant_filter=qdrant_filter
        )

        if use_rerank and hits and is_rerank_available():
            hits = cls._apply_rerank(query, hits)
        elif use_rerank and hits and not is_rerank_available():
            logger.warning("[KbRetrievalService] use_reranker=true 但 rerank 未配置，降级 recall 排序")

        if threshold is not None:
            hits = [h for h in hits if h.score >= threshold]

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:final_k]

    @classmethod
    def _apply_rerank(cls, query: str, hits: List[KbSearchHit]) -> List[KbSearchHit]:
        documents = [h.content for h in hits]
        try:
            ranked = rerank_documents(query, documents, top_n=len(documents))
        except Exception as exc:
            logger.warning(f"[KbRetrievalService] rerank 失败，降级 recall 排序: {exc}")
            return hits

        by_index = {i: h for i, h in enumerate(hits)}
        reranked: List[KbSearchHit] = []
        for idx, rerank_score in ranked:
            if idx not in by_index:
                continue
            hit = by_index[idx]
            recall_score = hit.recall_score if hit.recall_score is not None else hit.score
            reranked.append(
                KbSearchHit(
                    id=hit.id,
                    score=float(rerank_score),
                    content=hit.content,
                    file_name=hit.file_name,
                    search_mode=hit.search_mode,
                    header_path=hit.header_path,
                    recall_score=recall_score,
                    rerank_score=float(rerank_score),
                )
            )
        return reranked or hits

    @staticmethod
    def _matches_qdrant_filter(
        metadata: Dict[str, Any], qdrant_filter: Optional[Dict[str, Any]]
    ) -> bool:
        if not qdrant_filter:
            return True
        for key, value in qdrant_filter.items():
            meta_val = metadata.get(key)
            if key in ("file_name", "source_name") and meta_val is None:
                meta_val = metadata.get("file_name") or metadata.get("source_name")
            if str(meta_val or "") != str(value):
                return False
        return True

    @staticmethod
    def _hits_from_scored(
        scored: List[Tuple[Document, float]],
        mode: str,
        post_filter: Dict[str, Any],
        limit: int,
        *,
        qdrant_filter: Optional[Dict[str, Any]] = None,
    ) -> List[KbSearchHit]:
        hits: List[KbSearchHit] = []
        for doc, score in scored:
            meta = doc.metadata or {}
            if not KbRetrievalService._matches_qdrant_filter(meta, qdrant_filter):
                continue
            if not document_matches_post_filter(meta, post_filter):
                continue
            hit = KbRetrievalService._doc_to_hit(doc, score, mode)
            hit.recall_score = float(score)
            hits.append(hit)
            if len(hits) >= limit:
                break
        return hits

    @staticmethod
    def _doc_to_hit(doc: Document, score: float, mode: str) -> KbSearchHit:
        meta = doc.metadata or {}
        content = (
            doc.page_content
            or meta.get("clean_text")
            or meta.get("page_content")
            or ""
        )
        file_name = meta.get("file_name") or meta.get("source_name") or ""
        point_id = meta.get("point_id") or meta.get("content_hash") or meta.get("id") or ""
        return KbSearchHit(
            id=str(point_id),
            score=float(score),
            content=str(content)[:2000],
            file_name=str(file_name),
            search_mode=mode,
            header_path=meta.get("header_path") or None,
        )

    @classmethod
    def fetch_chunks_by_indexes(
        cls,
        collection_name: str,
        chunk_indexes: List[int],
    ) -> List[str]:
        """按逻辑 chunk_index 召回分片正文（测试用例等流程）。"""
        if not chunk_indexes or not is_qdrant_connected():
            return []

        client = get_qdrant_client()
        if not client:
            return []

        try:
            results, _ = client.scroll(
                collection_name=collection_name,
                limit=10000,
                with_payload=True,
            )
            if not results:
                return []

            by_chunk_index: Dict[int, str] = {}
            for point in results:
                payload = point.payload or {}
                raw_idx = payload.get("chunk_index")
                if raw_idx is None:
                    continue
                try:
                    ci = int(raw_idx)
                except (TypeError, ValueError):
                    continue
                text = (
                    payload.get("page_content")
                    or payload.get("content")
                    or payload.get("clean_text")
                    or ""
                )
                if text and ci not in by_chunk_index:
                    by_chunk_index[ci] = str(text)

            chunks: List[str] = []
            for idx in chunk_indexes:
                text = by_chunk_index.get(int(idx))
                if text:
                    chunks.append(text)
                else:
                    logger.warning(
                        f"[KbRetrievalService] chunk_index={idx} 未找到（已索引 {len(by_chunk_index)} 个逻辑分片）"
                    )
            return chunks
        except Exception as exc:
            logger.exception(f"[KbRetrievalService] 按索引召回失败: {exc}")
            return []

    @classmethod
    def fetch_full_document_by_file_name(
        cls,
        collection_name: str,
        file_name: str,
    ) -> str:
        """按 file_name 拉取该文档全部分片，按 chunk_index 排序后拼接为整篇正文。"""
        fn = (file_name or "").strip()
        if not fn or not is_qdrant_connected():
            return ""

        client = get_qdrant_client()
        if not client:
            return ""

        try:
            results, _ = client.scroll(
                collection_name=collection_name,
                limit=10000,
                with_payload=True,
            )
            shards: List[Tuple[int, str]] = []
            for point in results:
                payload = point.payload or {}
                if (payload.get("file_name") or payload.get("source_name") or "") != fn:
                    continue
                text = (
                    payload.get("page_content")
                    or payload.get("content")
                    or payload.get("clean_text")
                    or ""
                )
                if not str(text).strip():
                    continue
                raw_idx = payload.get("chunk_index")
                try:
                    sort_key = int(raw_idx) if raw_idx is not None else 10**9
                except (TypeError, ValueError):
                    sort_key = 10**9
                shards.append((sort_key, str(text)))

            if not shards:
                logger.warning(
                    f"[KbRetrievalService] file_name={fn} 在 collection={collection_name} 无分片"
                )
                return ""

            shards.sort(key=lambda x: x[0])
            return "\n\n".join(t for _, t in shards)
        except Exception as exc:
            logger.exception(
                f"[KbRetrievalService] 按 file_name 拉取整篇失败: {exc}"
            )
            return ""
