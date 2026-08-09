"""Small helpers shared by the five runtime owners."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from noesis.runtime.outcome import RuntimeOutcome, StopReason, set_runtime_outcome


def message_text(message: BaseMessage | None) -> str:
    if message is None:
        return ""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def response_messages(response: Any) -> list[BaseMessage]:
    result = getattr(response, "result", response)
    if isinstance(result, BaseMessage):
        return [result]
    return list(result or []) if isinstance(result, (list, tuple)) else []


def last_ai_message(response: Any) -> AIMessage | None:
    for message in reversed(response_messages(response)):
        if isinstance(message, AIMessage) or getattr(message, "type", None) == "ai":
            return message
    return None


def response_finish_reason(response: Any) -> str | None:
    message = last_ai_message(response)
    if message is None:
        return None
    metadata = dict(getattr(message, "response_metadata", {}) or {})
    additional = dict(getattr(message, "additional_kwargs", {}) or {})
    for source in (metadata, additional):
        raw = source.get("finish_reason") or source.get("stop_reason")
        if isinstance(raw, dict):
            raw = raw.get("reason") or raw.get("value")
        if raw:
            return str(raw)
    return None


def has_tool_side_effect(messages: list[BaseMessage]) -> bool:
    return any(getattr(message, "type", None) == "tool" for message in messages)


def set_outcome(value: RuntimeOutcome) -> RuntimeOutcome:
    set_runtime_outcome(value)
    return value


def reason_from_finish(value: str | None) -> StopReason | None:
    normalized = (value or "").strip().lower()
    mapping = {
        "length": StopReason.LENGTH_STOP,
        "max_tokens": StopReason.LENGTH_STOP,
        "length_stop": StopReason.LENGTH_STOP,
        "content_filter": StopReason.SAFETY_STOP,
        "safety": StopReason.SAFETY_STOP,
        "safety_stop": StopReason.SAFETY_STOP,
        "context_length_exceeded": StopReason.CONTEXT_EXHAUSTED,
        "context_exhausted": StopReason.CONTEXT_EXHAUSTED,
        "tool_loop_limit": StopReason.TOOL_LOOP_LIMIT,
        "tool_call_limit": StopReason.TOOL_CALL_LIMIT,
        "subagent_concurrency_limit": StopReason.SUBAGENT_CONCURRENCY_LIMIT,
        "subagent_total_limit": StopReason.SUBAGENT_TOTAL_LIMIT,
        "subagent_depth_limit": StopReason.SUBAGENT_DEPTH_LIMIT,
        "empty_after_tools": StopReason.EMPTY_AFTER_TOOLS,
        "partial_output": StopReason.PARTIAL_OUTPUT,
    }
    return mapping.get(normalized)


def content_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(content_size(item.get("text", "")) if isinstance(item, dict) else content_size(item) for item in value)
    return len(str(value or ""))


def tool_message_fields(message: ToolMessage) -> tuple[str | None, str | None]:
    metadata = dict(getattr(message, "additional_kwargs", {}) or {})
    status = getattr(message, "status", None) or metadata.get("status")
    result = metadata.get("outcome") or metadata.get("tool_outcome")
    return (str(status) if status else None, str(result) if result else None)
