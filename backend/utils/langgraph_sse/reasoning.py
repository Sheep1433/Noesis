"""从 LangChain AIMessageChunk 提取厂商思考增量（与 MODEL_TYPE 解耦）。"""

from __future__ import annotations

from typing import Any, Optional


def _coerce_reasoning_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        s = value
    else:
        s = str(value)
    return s if s else None


def extract_reasoning_delta(chunk: Any) -> Optional[str]:
    """
    从 astream_events 的 data.chunk 读取思考增量。

    优先级：additional_kwargs.reasoning_content → additional_kwargs.reasoning → chunk.reasoning_content
    """
    if chunk is None:
        return None

    kwargs = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(kwargs, dict):
        for key in ("reasoning_content", "reasoning"):
            delta = _coerce_reasoning_str(kwargs.get(key))
            if delta:
                return delta

    direct = _coerce_reasoning_str(getattr(chunk, "reasoning_content", None))
    if direct:
        return direct

    return None
