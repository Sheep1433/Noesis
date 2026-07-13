"""知识库模块：对外仅暴露 document_parse / chunk / embedding / retrieval 四层。"""
from __future__ import annotations

from kb.chunk import (
    KB_CHUNK_STRATEGY,
    chunk,
    deep_merge_mapping,
    fixed_processing_params,
    merge_query_execution_params,
    normalize_collection_processing_params,
    normalize_collection_query_params,
    resolve_effective_processing_params,
)
from kb.document_parse import DocumentParser, ParsedFile
from kb.embedding import get_embedding
from kb.retrieval import (
    KbRetrievalService,
    KbSearchHit,
    Retrieval,
    VectorStore,
    create_retrieval_system,
    kb_bm25_preprocess,
)

__all__ = [
    # document_parse
    "DocumentParser",
    "ParsedFile",
    # chunk
    "KB_CHUNK_STRATEGY",
    "chunk",
    "deep_merge_mapping",
    "fixed_processing_params",
    "merge_query_execution_params",
    "normalize_collection_processing_params",
    "normalize_collection_query_params",
    "resolve_effective_processing_params",
    # embedding
    "get_embedding",
    # retrieval
    "KbRetrievalService",
    "KbSearchHit",
    "Retrieval",
    "VectorStore",
    "create_retrieval_system",
    "kb_bm25_preprocess",
]
