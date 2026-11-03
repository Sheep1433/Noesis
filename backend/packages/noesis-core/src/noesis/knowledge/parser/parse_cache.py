"""DeepDoc 解析结果 JSON 缓存（`.noesis/kb_parse/`）。"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from noesis.config.paths import data_path
from noesis.fsutil import atomic_write_text
from noesis.knowledge.parser.deepdoc_config import DEEPDOC_UPSTREAM_COMMIT
from noesis.knowledge.parser.deepdoc_result import DeepDocBlock, DeepDocParseResult, DeepDocTable
from noesis.runtime.logging import logger

# Noesis 侧解析行为版本（区别于 deepdoc 上游 commit：deepdoc_version 只钉上游
# 移植代码，我们自己解析行为的变更不在其内）。解析行为变更时 +1，旧缓存
# 自动失效——否则重导入会静默回放旧切块（10k 重导实测：段落对齐 0/434）。
# 2 = 纯文本整篇单块（2026-11-01）；此前 1 = RAGFlow txt 128 token 预切。
PARSE_BEHAVIOR_VERSION = 2


def _cache_root() -> Path:
    return data_path("kb_parse")


def cache_file_path(collection_name: str, file_hash: str) -> Path:
    safe_col = (collection_name or "default").replace("/", "_")
    fid = (file_hash or "")[:32]
    return _cache_root() / safe_col / f"{fid}.json"


def _block_from_dict(raw: dict) -> DeepDocBlock:
    return DeepDocBlock(
        content=str(raw.get("content") or ""),
        page_no=raw.get("page_no"),
        layout_type=raw.get("layout_type"),
        bbox=raw.get("bbox"),
        metadata=dict(raw.get("metadata") or {}),
    )


def _table_from_dict(raw: dict) -> DeepDocTable:
    return DeepDocTable(
        content=str(raw.get("content") or ""),
        page_no=raw.get("page_no"),
        metadata=dict(raw.get("metadata") or {}),
    )


def result_from_dict(data: dict[str, Any]) -> DeepDocParseResult:
    return DeepDocParseResult(
        source_file_name=str(data.get("source_file_name") or ""),
        file_path=str(data.get("file_path") or ""),
        file_type=str(data.get("file_type") or ""),
        blocks=[_block_from_dict(b) for b in (data.get("blocks") or [])],
        tables=[_table_from_dict(t) for t in (data.get("tables") or [])],
        figures=[_block_from_dict(b) for b in (data.get("figures") or [])],
        parser_id=str(data.get("parser_id") or "deepdoc"),
        deepdoc_version=str(data.get("deepdoc_version") or ""),
        update_time=str(data.get("update_time") or ""),
        domain=data.get("domain"),
        business=data.get("business"),
    )


def load_parse_cache(
    collection_name: str,
    file_hash: str,
    *,
    expected_deepdoc_version: Optional[str] = None,
) -> Optional[DeepDocParseResult]:
    path = cache_file_path(collection_name, file_hash)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        behavior = str(data.get("parse_behavior_version") or "1")
        if behavior != str(PARSE_BEHAVIOR_VERSION):
            logger.info(f"[parse_cache] 解析行为版本不匹配（{behavior}），忽略缓存: {path.name}")
            return None
        version = str(data.get("deepdoc_version") or "")
        pin = (expected_deepdoc_version or DEEPDOC_UPSTREAM_COMMIT[:12]).strip()
        if pin and version and not version.startswith(pin[:12]):
            logger.info(f"[parse_cache] 版本不匹配，忽略缓存: {path.name}")
            return None
        return result_from_dict(data)
    except Exception as exc:
        logger.warning(f"[parse_cache] 读取失败 {path}: {exc}")
        return None


def save_parse_cache(
    collection_name: str,
    file_hash: str,
    result: DeepDocParseResult,
) -> Path:
    path = cache_file_path(collection_name, file_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(result)
    payload["file_hash"] = file_hash
    payload["collection_name"] = collection_name
    payload["parse_behavior_version"] = PARSE_BEHAVIOR_VERSION
    # 共享目录上并发写/读同一缓存：原子替换，读方不取半截 JSON
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info(f"[parse_cache] 已写入 {path}")
    return path
