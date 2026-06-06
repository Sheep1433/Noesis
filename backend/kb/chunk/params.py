"""
入库 processing_params / 检索 query_params 合并与默认值。

分块策略固定为 markdown_headers；chunk_size / chunk_overlap 为平台常量。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, MutableMapping, Optional

KB_CHUNK_STRATEGY = "markdown_headers"


def deep_merge_mapping(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    """浅层键合并 + 值为 dict 时递归合并；override 中非 dict 叶子覆盖 base。"""
    out: Dict[str, Any] = dict(base)
    for k, v in override.items():
        if (
            k in out
            and isinstance(out[k], MutableMapping)
            and isinstance(v, Mapping)
            and not isinstance(v, type(...))
        ):
            out[k] = deep_merge_mapping(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def resolve_effective_processing_params(
    *,
    collection_defaults: Optional[Mapping[str, Any]] = None,
    document_overrides: Optional[Mapping[str, Any]] = None,
    request_once: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    优先级（由低到高）：集合默认 → 文档持久化覆盖 → 仅当次 ingest 的 request_once。
    合并结果始终固定 strategy=markdown_headers。
    """
    merged = dict(collection_defaults or {})
    if document_overrides:
        merged = deep_merge_mapping(merged, document_overrides)
    if request_once:
        merged = deep_merge_mapping(merged, request_once)
    merged["strategy"] = KB_CHUNK_STRATEGY
    return merged


def merge_query_execution_params(
    *,
    persisted: Optional[Mapping[str, Any]] = None,
    request_overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    检索执行参数：持久化默认值 + 单次请求覆盖（HTTP body 中非 null 字段优先）。
    """
    base = dict(persisted or {})
    if not request_overrides:
        return base
    for k, v in request_overrides.items():
        if v is not None:
            base[k] = v
    return base


DEFAULT_COLLECTION_PROCESSING: Dict[str, Any] = {
    "chunk_size": 500,
    "chunk_overlap": 50,
    "strategy": KB_CHUNK_STRATEGY,
}
DEFAULT_COLLECTION_QUERY: Dict[str, Any] = {"limit": 10, "score_threshold": None}


def _normalize_chunk_params(effective_params: Mapping[str, Any]) -> tuple[int, int]:
    params = dict(effective_params or {})
    chunk_size = max(int(params.get("chunk_size") or 500), 32)
    chunk_overlap = int(params.get("chunk_overlap") or 50)
    chunk_overlap = min(max(chunk_overlap, 0), max(chunk_size // 2, 1))
    return chunk_size, chunk_overlap


def _fixed_window_chunks(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    """内部容错：滑窗切片。"""
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunks.append(text[start:end])
        if end >= text_length:
            break
        start = max(end - overlap, start + 1)
    return chunks


def normalize_mysql_processing_params(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """与 DEFAULT_COLLECTION_PROCESSING 合并，并固定 Markdown 标题分块。"""
    merged = deep_merge_mapping(DEFAULT_COLLECTION_PROCESSING, raw or {})
    merged["strategy"] = KB_CHUNK_STRATEGY
    return merged


def normalize_mysql_query_params(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """与 DEFAULT_COLLECTION_QUERY 合并。"""
    return deep_merge_mapping(DEFAULT_COLLECTION_QUERY, raw or {})


def fixed_processing_params() -> Dict[str, Any]:
    """平台固定入库分块参数。"""
    return normalize_mysql_processing_params({})
