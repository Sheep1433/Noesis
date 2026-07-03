"""Qdrant 分片展示字段：与 payload 读写约定对齐的纯函数。"""
from __future__ import annotations

from typing import Any, Dict, Optional


def payload_created_at(payload: Dict[str, Any]) -> Optional[str]:
    """分片入库时间：读取 created_at，兼容历史数据的 update_time。"""
    value = payload.get("created_at") or payload.get("update_time")
    return str(value) if value else None


def vector_length(vector: Any) -> int:
    if not vector:
        return 0
    if isinstance(vector, dict):
        first = next(iter(vector.values()), None)
        return len(first) if first else 0
    return len(vector)
