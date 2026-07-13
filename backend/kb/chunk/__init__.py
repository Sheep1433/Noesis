"""分块层：ParsedFile / Markdown → 分片 Document。"""
from __future__ import annotations

from kb.chunk.chunker import chunk
from kb.chunk.params import (
    DEFAULT_COLLECTION_PROCESSING,
    DEFAULT_COLLECTION_QUERY,
    KB_CHUNK_PRESET_GENERAL,
    KB_CHUNK_STRATEGY,
    KB_CHUNK_TEMPLATE_GENERAL,
    KB_CHUNK_TEMPLATES_IMPLEMENTED,
    KB_CHUNK_TEMPLATES_RESERVED,
    build_effective_processing_snapshot,
    deep_merge_mapping,
    fixed_processing_params,
    merge_query_execution_params,
    normalize_collection_processing_params,
    normalize_collection_query_params,
    normalize_query_execution_params,
    resolve_effective_processing_params,
)

__all__ = [
    "DEFAULT_COLLECTION_PROCESSING",
    "DEFAULT_COLLECTION_QUERY",
    "KB_CHUNK_PRESET_GENERAL",
    "KB_CHUNK_STRATEGY",
    "KB_CHUNK_TEMPLATE_GENERAL",
    "KB_CHUNK_TEMPLATES_IMPLEMENTED",
    "KB_CHUNK_TEMPLATES_RESERVED",
    "build_effective_processing_snapshot",
    "chunk",
    "deep_merge_mapping",
    "fixed_processing_params",
    "merge_query_execution_params",
    "normalize_collection_processing_params",
    "normalize_collection_query_params",
    "normalize_query_execution_params",
    "resolve_effective_processing_params",
]
