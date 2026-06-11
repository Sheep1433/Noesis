"""Shared context window metrics for summarization and UI display."""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware.summarization import TokenCounter, count_tokens_approximately
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.utils.function_calling import convert_to_openai_tool

from config.env import ModelConfig
from utils.log_util import logger

DEFAULT_CONTEXT_MAX_INPUT_TOKENS = 128_000


def get_agent_token_counter() -> TokenCounter:
    return count_tokens_approximately


def resolve_context_max_tokens() -> int:
    if ModelConfig.context_max_input_tokens > 0:
        return int(ModelConfig.context_max_input_tokens)
    logger.warning(
        "context.max_input_tokens 未配置，使用默认值 {}",
        DEFAULT_CONTEXT_MAX_INPUT_TOKENS,
    )
    return DEFAULT_CONTEXT_MAX_INPUT_TOKENS


def _messages_with_system(
    messages: list[Any],
    system_message: SystemMessage | None = None,
) -> list[Any]:
    if system_message is not None:
        return [system_message, *messages]
    return list(messages)


def _openai_tool_defs(tools: list[Any]) -> list[dict[str, Any]]:
    defs: list[dict[str, Any]] = []
    for tool in tools:
        if isinstance(tool, dict):
            defs.append(tool)
            continue
        try:
            defs.append(convert_to_openai_tool(tool))
        except Exception:
            name = getattr(tool, "name", "") or "tool"
            desc = getattr(tool, "description", "") or ""
            defs.append({"type": "function", "function": {"name": name, "description": desc}})
    return defs


def _estimate_tools_tokens_approx(tools: list[Any]) -> int:
    if not tools:
        return 0
    blob = json.dumps(_openai_tool_defs(tools), ensure_ascii=False)
    return int(count_tokens_approximately([SystemMessage(content=blob)]))


def _serialize_request_payload(messages: list[Any], tools: list[Any]) -> str:
    payload: dict[str, Any] = {"messages": convert_to_openai_messages(messages)}
    if tools:
        payload["tools"] = _openai_tool_defs(tools)
    return json.dumps(payload, ensure_ascii=False)


def estimate_model_request_input_tokens(request: ModelRequest) -> int:
    """估算单次模型调用的输入 token（对话 + system + tools）。"""
    messages = _messages_with_system(list(request.messages), request.system_message)
    tools = list(request.tools or [])
    get_num_tokens = getattr(request.model, "get_num_tokens", None)
    if callable(get_num_tokens):
        return int(get_num_tokens(_serialize_request_payload(messages, tools)))
    return int(get_agent_token_counter()(messages)) + _estimate_tools_tokens_approx(tools)


def compute_used_percentage(current_tokens: int, max_tokens: int) -> int:
    """占用百分比；有占用但四舍五入为 0 时显示 1%，避免圆环长期为 0%。"""
    if max_tokens <= 0 or current_tokens <= 0:
        return 0
    pct = round(current_tokens / max_tokens * 100)
    if pct == 0:
        return 1
    return min(100, pct)


def build_context_snapshot(messages: list[Any]) -> dict[str, int]:
    """仅基于消息列表的粗估（摘要触发等内部逻辑使用）。"""
    current_tokens = int(get_agent_token_counter()(messages))
    max_tokens = resolve_context_max_tokens()
    used_percentage = compute_used_percentage(current_tokens, max_tokens)
    return {
        "current_tokens": current_tokens,
        "max_tokens": max_tokens,
        "used_percentage": used_percentage,
    }


def build_context_snapshot_from_request(request: ModelRequest) -> dict[str, int]:
    """Composer 上下文指示器：对齐即将发往模型的有效输入规模。"""
    current_tokens = estimate_model_request_input_tokens(request)
    max_tokens = resolve_context_max_tokens()
    used_percentage = compute_used_percentage(current_tokens, max_tokens)
    return {
        "current_tokens": current_tokens,
        "max_tokens": max_tokens,
        "used_percentage": used_percentage,
    }
