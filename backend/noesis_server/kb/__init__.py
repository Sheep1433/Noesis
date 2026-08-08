"""Re-export ``noesis.knowledge`` engine under legacy ``noesis_server.kb`` paths.

Authoritative implementation: ``noesis.knowledge``. Each legacy subpackage name
(``document_parse``, ``chunk``, ``retrieval``, ``rerank``, ``embedding``) is
aliased to its harness canonical module via ``sys.modules`` so deep import paths
(e.g. ``noesis.knowledge.document_parse.staging``) keep resolving. Removed once
all consumers import ``noesis.knowledge`` directly.
"""
from __future__ import annotations

import sys
from importlib import import_module

_LEGACY_ALIAS = {
    "document_parse": "noesis.knowledge.parser",
    "chunk": "noesis.knowledge.chunking",
    "retrieval": "noesis.knowledge.retrieval",
    "rerank": "noesis.knowledge.rerank",
    "embedding": "noesis.knowledge.embedding",
}

for _legacy, _canonical in _LEGACY_ALIAS.items():
    _mod = import_module(_canonical)
    sys.modules[f"noesis.knowledge.{_legacy}"] = _mod

_filters = import_module("noesis.knowledge.retrieval.filters")
sys.modules["noesis.knowledge.filters"] = _filters
sys.modules["noesis.knowledge.retrieval.filters"] = _filters

from noesis.knowledge.chunking import (  # noqa: E402,F401
    KB_CHUNK_STRATEGY,
    build_effective_processing_snapshot,
    chunk,
    deep_merge_mapping,
    fixed_processing_params,
    merge_query_execution_params,
    normalize_collection_processing_params,
    normalize_collection_query_params,
    normalize_query_execution_params,
    resolve_effective_processing_params,
)
from noesis.knowledge.parser import DocumentParser, ParsedFile  # noqa: E402,F401
from noesis.knowledge.embedding import get_embedding  # noqa: E402,F401
from noesis.knowledge.retrieval import (  # noqa: E402,F401
    KbRetrievalService,
    KbSearchHit,
    Retrieval,
    VectorStore,
    create_retrieval_system,
    kb_bm25_preprocess,
)

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
    "KbRetrievalService",
    "KbSearchHit",
    "Retrieval",
    "VectorStore",
    "create_retrieval_system",
    "kb_bm25_preprocess",
]
