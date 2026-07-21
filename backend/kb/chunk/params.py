"""
入库 processing_params / 检索 query_params 合并与默认值。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, MutableMapping, Optional

from common.logging import logger

KB_CHUNK_PRESET_GENERAL = "general"
KB_CHUNK_TEMPLATE_GENERAL = "general"
KB_CHUNK_STRATEGY = "markdown_headers"  # legacy 别名，读入时规范化为 general
CHUNK_ENGINE_VERSION = "1"

KB_CHUNK_TEMPLATES_IMPLEMENTED = frozenset({KB_CHUNK_TEMPLATE_GENERAL})
KB_CHUNK_TEMPLATES_RESERVED = frozenset({"book", "paper", "laws", "qa", "manual", "table"})


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


def _normalize_chunk_template(raw: Mapping[str, Any]) -> str:
    template = (
        raw.get("chunk_template_id")
        or raw.get("chunk_preset_id")
        or raw.get("strategy")
        or KB_CHUNK_TEMPLATE_GENERAL
    )
    template = str(template or KB_CHUNK_TEMPLATE_GENERAL).strip().lower()
    if template in (KB_CHUNK_STRATEGY, "markdown_headers", "", "none"):
        return KB_CHUNK_TEMPLATE_GENERAL
    if template in KB_CHUNK_TEMPLATES_IMPLEMENTED:
        return template
    if template in KB_CHUNK_TEMPLATES_RESERVED:
        logger.warning(
            f"[kb.chunk] chunk_template_id={template} 尚未实现，回退 {KB_CHUNK_TEMPLATE_GENERAL}"
        )
    elif template != KB_CHUNK_TEMPLATE_GENERAL:
        logger.warning(
            f"[kb.chunk] 未知 chunk_template_id={template}，回退 {KB_CHUNK_TEMPLATE_GENERAL}"
        )
    return KB_CHUNK_TEMPLATE_GENERAL


def _normalize_chunk_preset(raw: Mapping[str, Any]) -> str:
    return _normalize_chunk_template(raw)


def _flatten_chunk_parser_config(merged: Dict[str, Any]) -> Dict[str, Any]:
    cpc = dict(merged.get("chunk_parser_config") or {})
    if "chunk_size" in merged:
        cpc["chunk_size"] = merged.pop("chunk_size")
    if "chunk_overlap" in merged:
        cpc["chunk_overlap"] = merged.pop("chunk_overlap")
    merged["chunk_parser_config"] = {
        "chunk_size": int(cpc.get("chunk_size") or 500),
        "chunk_overlap": int(cpc.get("chunk_overlap") or 50),
    }
    merged.pop("strategy", None)
    template = _normalize_chunk_template(merged)
    merged["chunk_template_id"] = template
    merged["chunk_preset_id"] = template
    parser_id = (merged.get("parser_id") or "deepdoc").strip().lower()
    if parser_id != "deepdoc":
        parser_id = "deepdoc"
    merged["parser_id"] = parser_id
    return merged


def resolve_effective_processing_params(
    *,
    collection_defaults: Optional[Mapping[str, Any]] = None,
    document_overrides: Optional[Mapping[str, Any]] = None,
    request_once: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    优先级（由低到高）：集合默认 → 文档持久化覆盖 → 仅当次 ingest 的 request_once。
    """
    merged = dict(collection_defaults or {})
    if document_overrides:
        merged = deep_merge_mapping(merged, document_overrides)
    if request_once:
        merged = deep_merge_mapping(merged, request_once)
    merged = _flatten_chunk_parser_config(merged)
    merged["chunk_engine_version"] = CHUNK_ENGINE_VERSION
    return merged


def _apply_limit_alias(params: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(params)
    limit_val = out.pop("limit", None)
    if limit_val is not None:
        if out.get("final_top_k") is None:
            out["final_top_k"] = limit_val
    return out


def merge_query_execution_params(
    *,
    persisted: Optional[Mapping[str, Any]] = None,
    request_overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    检索执行参数：持久化默认值 + 单次请求覆盖（HTTP body 中非 null 字段优先）。
    """
    base = _apply_limit_alias(dict(persisted or {}))
    if not request_overrides:
        return base
    cleaned: Dict[str, Any] = {}
    for k, v in request_overrides.items():
        if v is not None:
            cleaned[k] = v
    merged = deep_merge_mapping(base, cleaned)
    if "limit" in cleaned:
        merged["final_top_k"] = cleaned["limit"]
    merged.pop("limit", None)
    return merged


def normalize_query_execution_params(
    *,
    collection_query: Optional[Mapping[str, Any]] = None,
    request_overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """平台默认 → 集合 query_params → 单次请求覆盖。"""
    base = normalize_collection_query_params(collection_query)
    merged = merge_query_execution_params(persisted=base, request_overrides=request_overrides)
    for key, default in DEFAULT_COLLECTION_QUERY.items():
        if key == "limit":
            continue
        if merged.get(key) is None and default is not None:
            merged[key] = default
    return _apply_limit_alias(merged)


DEFAULT_COLLECTION_PROCESSING: Dict[str, Any] = {
    "chunk_preset_id": KB_CHUNK_PRESET_GENERAL,
    "chunk_template_id": KB_CHUNK_TEMPLATE_GENERAL,
    "parser_id": "deepdoc",
    "chunk_parser_config": {
        "chunk_size": 500,
        "chunk_overlap": 50,
    },
}

DEFAULT_COLLECTION_QUERY: Dict[str, Any] = {
    "search_mode": "hybrid",
    "use_reranker": True,
    # 成本优先：缩小召回与 rerank 文档数（DashScope 按 documents 计费）
    "recall_top_k": 20,
    "rerank_top_k": 15,
    "final_top_k": 10,
    "score_threshold": None,
    "rrf_k": 60,
}


def _normalize_chunk_params(effective_params: Mapping[str, Any]) -> tuple[int, int]:
    params = dict(effective_params or {})
    cpc = params.get("chunk_parser_config") or {}
    if not isinstance(cpc, Mapping):
        cpc = {}
    chunk_size = max(int(cpc.get("chunk_size") or params.get("chunk_size") or 500), 32)
    chunk_overlap = int(cpc.get("chunk_overlap") or params.get("chunk_overlap") or 50)
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


def normalize_collection_processing_params(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """与 DEFAULT_COLLECTION_PROCESSING 合并，并规范化为 general preset。"""
    merged = deep_merge_mapping(DEFAULT_COLLECTION_PROCESSING, raw or {})
    return _flatten_chunk_parser_config(merged)


def normalize_collection_query_params(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """与 DEFAULT_COLLECTION_QUERY 合并；limit 别名映射为 final_top_k。"""
    raw_dict = dict(raw or {})
    limit_only = "limit" in raw_dict and "final_top_k" not in raw_dict
    merged = deep_merge_mapping(DEFAULT_COLLECTION_QUERY, raw_dict)
    if limit_only and raw_dict.get("limit") is not None:
        merged["final_top_k"] = raw_dict["limit"]
    merged.pop("limit", None)
    return merged


def fixed_processing_params() -> Dict[str, Any]:
    """平台固定入库分块参数。"""
    return normalize_collection_processing_params({})


def build_effective_processing_snapshot(effective_params: Mapping[str, Any]) -> Dict[str, Any]:
    """写入 Qdrant payload 的 effective_processing_params 快照。"""
    normalized = normalize_collection_processing_params(effective_params)
    snapshot: Dict[str, Any] = {
        "chunk_preset_id": normalized.get("chunk_preset_id", KB_CHUNK_PRESET_GENERAL),
        "chunk_template_id": normalized.get(
            "chunk_template_id", normalized.get("chunk_preset_id", KB_CHUNK_TEMPLATE_GENERAL)
        ),
        "chunk_parser_config": dict(normalized.get("chunk_parser_config") or {}),
        "parser_id": normalized.get("parser_id", "deepdoc"),
        "chunk_engine_version": CHUNK_ENGINE_VERSION,
    }
    if normalized.get("parser_id") == "deepdoc":
        version = str(effective_params.get("deepdoc_version") or "").strip()
        if version:
            snapshot["deepdoc_version"] = version
    return snapshot
