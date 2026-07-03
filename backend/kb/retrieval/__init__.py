"""检索层：向量存储、检索策略与统一检索门面。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from kb.retrieval.filters import document_matches_post_filter, split_search_filters
from kb.retrieval.payload import build_payload, documents_to_points
from kb.retrieval.service import KbRetrievalService, KbSearchHit, KbSearchResult, KbSearchTiming
from kb.retrieval.store import (
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

if TYPE_CHECKING:
    pass
