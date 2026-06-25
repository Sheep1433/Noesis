"""从 LangChain AIMessage / AIMessageChunk 提取正文与思考增量（与 MODEL_TYPE 解耦）。"""

from __future__ import annotations

from typing import Any, List, Optional


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


def extract_text_content(message: Any) -> str:
    """从 AIMessage / AIMessageChunk 提取可见正文（支持 str 与 content blocks）。"""
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    if content:
        return str(content)
    return ""


def unsent_text_suffix(full_text: str, sent_text: str) -> str:
    """流式已发正文与终态全文对比，返回尚未下发的后缀。"""
    if not full_text:
        return ""
    if not sent_text:
        return full_text
    if full_text.startswith(sent_text):
        return full_text[len(sent_text):]
    return ""
