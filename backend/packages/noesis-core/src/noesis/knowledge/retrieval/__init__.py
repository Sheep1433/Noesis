"""检索层：向量存储、检索策略与统一检索门面。"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from noesis.knowledge.retrieval.filters import document_matches_post_filter, split_search_filters
from noesis.knowledge.retrieval.payload import build_payload, documents_to_points
from noesis.knowledge.retrieval.store import (
    Retrieval,
    VectorStore,
    create_retrieval_system,
    kb_bm25_preprocess,
)

__all__ = [
    "KbRetrievalService",
    "KbSearchHit",
    "KbSearchResult",
    "KbSearchTiming",
    "Retrieval",
    "VectorStore",
    "build_payload",
    "create_retrieval_system",
    "document_matches_post_filter",
    "documents_to_points",
    "kb_bm25_preprocess",
    "split_search_filters",
]

_SERVICE_EXPORTS = frozenset(
    {"KbRetrievalService", "KbSearchHit", "KbSearchResult", "KbSearchTiming"}
)


def __getattr__(name: str) -> Any:
    """延迟加载检索门面，避免 Qdrant adapter 与 runtime 形成导入环。"""
    if name in _SERVICE_EXPORTS:
        return getattr(import_module("noesis.knowledge.retrieval.service"), name)
    raise AttributeError(name)


if TYPE_CHECKING:
    from noesis.knowledge.retrieval.service import (
        KbRetrievalService,
        KbSearchHit,
        KbSearchResult,
        KbSearchTiming,
    )
