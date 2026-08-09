"""Noesis knowledge subsystem — RAG engine (parse / chunk / embed / retrieve / rerank).

Lazy-loaded: importing ``noesis`` or ``noesis.factory`` does not pull this
subsystem. Use ``from noesis.knowledge import KbRetrievalService`` etc. to load
on demand. Heavy DeepDoc dependencies (ONNX / PaddleOCR) load only on first
actual parse, not at facade import.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "DocumentParser",
    "ParsedFile",
    "KB_CHUNK_STRATEGY",
    "chunk",
    "deep_merge_mapping",
    "fixed_processing_params",
    "merge_query_execution_params",
    "normalize_collection_processing_params",
    "normalize_collection_query_params",
    "normalize_query_execution_params",
    "resolve_effective_processing_params",
    "build_effective_processing_snapshot",
    "get_embedding",
    "is_embedding_configured",
    "is_vlm_configured",
    "embedding_not_configured_message",
    "KbRetrievalService",
    "KbSearchHit",
    "KbSearchResult",
    "KbSearchTiming",
    "Retrieval",
    "VectorStore",
    "create_retrieval_system",
    "kb_bm25_preprocess",
    "KnowledgeBase",
    "KnowledgeBaseFactory",
    "KnowledgeBaseManager",
    "knowledge_base",
]


def __getattr__(name: str) -> Any:
    if name in {
        "DocumentParser",
        "ParsedFile",
    }:
        from noesis.knowledge.parser import DocumentParser, ParsedFile
        globals()["DocumentParser"] = DocumentParser
        globals()["ParsedFile"] = ParsedFile
        return globals()[name]

    if name in {
        "KB_CHUNK_STRATEGY",
        "chunk",
        "deep_merge_mapping",
        "fixed_processing_params",
        "merge_query_execution_params",
        "normalize_collection_processing_params",
        "normalize_collection_query_params",
        "normalize_query_execution_params",
        "resolve_effective_processing_params",
        "build_effective_processing_snapshot",
    }:
        from noesis.knowledge import chunking
        mod = {
            "KB_CHUNK_STRATEGY": chunking.KB_CHUNK_STRATEGY,
            "chunk": chunking.chunk,
            "deep_merge_mapping": chunking.deep_merge_mapping,
            "fixed_processing_params": chunking.fixed_processing_params,
            "merge_query_execution_params": chunking.merge_query_execution_params,
            "normalize_collection_processing_params": chunking.normalize_collection_processing_params,
            "normalize_collection_query_params": chunking.normalize_collection_query_params,
            "normalize_query_execution_params": chunking.normalize_query_execution_params,
            "resolve_effective_processing_params": chunking.resolve_effective_processing_params,
            "build_effective_processing_snapshot": chunking.build_effective_processing_snapshot,
        }
        globals().update(mod)
        return globals()[name]

    if name in {"get_embedding", "is_embedding_configured", "is_vlm_configured", "embedding_not_configured_message"}:
        from noesis.knowledge.embedding import (
            embedding_not_configured_message,
            get_embedding,
            is_embedding_configured,
            is_vlm_configured,
        )
        globals()["get_embedding"] = get_embedding
        globals()["is_embedding_configured"] = is_embedding_configured
        globals()["is_vlm_configured"] = is_vlm_configured
        globals()["embedding_not_configured_message"] = embedding_not_configured_message
        return globals()[name]

    if name in {
        "KbRetrievalService",
        "KbSearchHit",
        "KbSearchResult",
        "KbSearchTiming",
        "Retrieval",
        "VectorStore",
        "create_retrieval_system",
        "kb_bm25_preprocess",
    }:
        from noesis.knowledge.retrieval import (
            KbRetrievalService,
            KbSearchHit,
            KbSearchResult,
            KbSearchTiming,
            Retrieval,
            VectorStore,
            create_retrieval_system,
            kb_bm25_preprocess,
        )
        globals().update(
            KbRetrievalService=KbRetrievalService,
            KbSearchHit=KbSearchHit,
            KbSearchResult=KbSearchResult,
            KbSearchTiming=KbSearchTiming,
            Retrieval=Retrieval,
            VectorStore=VectorStore,
            create_retrieval_system=create_retrieval_system,
            kb_bm25_preprocess=kb_bm25_preprocess,
        )
        return globals()[name]

    if name == "KnowledgeBase":
        from noesis.knowledge.base import KnowledgeBase
        globals()["KnowledgeBase"] = KnowledgeBase
        return KnowledgeBase

    if name == "KnowledgeBaseFactory":
        from noesis.knowledge.factory import KnowledgeBaseFactory
        globals()["KnowledgeBaseFactory"] = KnowledgeBaseFactory
        return KnowledgeBaseFactory

    if name == "KnowledgeBaseManager":
        from noesis.knowledge.manager import KnowledgeBaseManager
        globals()["KnowledgeBaseManager"] = KnowledgeBaseManager
        return KnowledgeBaseManager

    if name == "knowledge_base":
        from noesis.knowledge.runtime import knowledge_base
        globals()["knowledge_base"] = knowledge_base
        return knowledge_base

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
