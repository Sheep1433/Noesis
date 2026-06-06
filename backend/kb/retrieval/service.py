"""知识库统一检索门面。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

if TYPE_CHECKING:
    from kb.retrieval.store import Retrieval

from config.env import QdrantConfig
from kb.retrieval.filters import document_matches_post_filter, split_search_filters
from services.qdrant_service import get_qdrant_client, is_qdrant_connected
from utils.log_util import logger

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


class KbRetrievalService:
    """集合内检索：search_mode + filters + 平台默认 query 参数与请求体合并。"""

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
        search_mode: str = "vector",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None,
        rrf_k: int = 60,
        vector_dimension: int = 1024,
    ) -> List[KbSearchHit]:
        if not is_qdrant_connected():
            raise RuntimeError("向量库未连接")

        mode = (search_mode or "vector").strip().lower()
        if mode not in _VALID_MODES:
            raise ValueError(f"不支持的 search_mode: {search_mode}")

        qdrant_filter, post_filter = split_search_filters(filters)
        retrieval = cls._get_retrieval(collection_name, vector_dimension)

        if mode == "vector":
            scored = retrieval.vector_search(
                query,
                k=limit,
                score_threshold=score_threshold,
                metadata_filter=qdrant_filter,
            )
            return cls._hits_from_scored(scored, mode, post_filter, limit)

        if mode == "bm25":
            scored = retrieval.bm25_search_with_scores(
                query, k=limit * 3 if post_filter else limit
            )
            return cls._hits_from_scored(scored, mode, post_filter, limit)

        scored = retrieval.hybrid_search_with_scores(
            query,
            k=limit * 3 if post_filter else limit,
            rrf_k=rrf_k,
            metadata_filter=qdrant_filter,
        )
        return cls._hits_from_scored(scored, mode, post_filter, limit)

    @staticmethod
    def _apply_post_filter_docs(
        documents: List[Document], post_filter: Dict[str, Any]
    ) -> List[Document]:
        if not post_filter:
            return documents
        return [d for d in documents if document_matches_post_filter(d.metadata, post_filter)]

    @staticmethod
    def _hits_from_scored(
        scored: List[Tuple[Document, float]],
        mode: str,
        post_filter: Dict[str, Any],
        limit: int,
    ) -> List[KbSearchHit]:
        hits: List[KbSearchHit] = []
        for doc, score in scored:
            if not document_matches_post_filter(doc.metadata, post_filter):
                continue
            hits.append(KbRetrievalService._doc_to_hit(doc, score, mode))
            if len(hits) >= limit:
                break
        return hits

    @staticmethod
    def _hits_from_docs(
        documents: List[Document], mode: str, default_score: float
    ) -> List[KbSearchHit]:
        return [
            KbRetrievalService._doc_to_hit(doc, default_score, mode) for doc in documents
        ]

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
