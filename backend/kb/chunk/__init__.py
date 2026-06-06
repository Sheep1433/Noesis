"""分块层：ParsedFile / Markdown → 分片 Document。"""
from __future__ import annotations

from kb.chunk.chunker import chunk
from kb.chunk.params import (
    DEFAULT_COLLECTION_PROCESSING,
    DEFAULT_COLLECTION_QUERY,
    KB_CHUNK_STRATEGY,
    deep_merge_mapping,
    fixed_processing_params,
    merge_query_execution_params,
    normalize_mysql_processing_params,
    normalize_mysql_query_params,
    resolve_effective_processing_params,
)

__all__ = [
    "DEFAULT_COLLECTION_PROCESSING",
    "DEFAULT_COLLECTION_QUERY",
    "KB_CHUNK_STRATEGY",
    "chunk",
    "deep_merge_mapping",
    "fixed_processing_params",
    "merge_query_execution_params",
    "normalize_mysql_processing_params",
    "normalize_mysql_query_params",
    "resolve_effective_processing_params",
]
