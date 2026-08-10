"""Context window metrics: token estimation for internal limits and occupancy.

Only ``estimate_model_request_input_tokens`` (used by ContextLifecycleMiddleware
for context-exhaustion interception before the model call) and the shared
``count_tokens_approximately`` counter (used by SummarizationMiddleware for
compaction triggering) remain as estimation paths. The user-facing context
indicator no longer uses these — it displays the provider's real
``input_tokens`` from ``usage_metadata``, written back to
``ContextMetricsRegistry`` by the SSE bridge after each model call.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware.summarization import TokenCounter, count_tokens_approximately
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.utils.function_calling import convert_to_openai_tool


def get_agent_token_counter() -> TokenCounter:
    return count_tokens_approximately


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


def _estimate_input_tokens_with_method(request: ModelRequest) -> int:
    """估算输入 token（对话 + system + tools），供 model call 前的拦截使用。

    优先使用模型公开 tokenizer（``model.get_num_tokens``）；不可用时回退到
    LangChain approximate counter。此值仅用于上下文耗尽拦截和摘要触发，
    不作为用户可见的上下文占用——用户可见值由 provider 真实 input_tokens 提供。
    """
    messages = _messages_with_system(list(request.messages), request.system_message)
    tools = list(request.tools or [])
    get_num_tokens = getattr(request.model, "get_num_tokens", None)
    if callable(get_num_tokens):
        try:
            return int(get_num_tokens(_serialize_request_payload(messages, tools)))
        except (ImportError, RuntimeError, ValueError, TypeError):
            # Some test/fake models expose the LangChain method but rely on an
            # optional tokenizer package.  The shared approximate counter is a
            # safe fallback for the internal exhaustion check.
            pass
    return int(get_agent_token_counter()(messages)) + _estimate_tools_tokens_approx(tools)


def estimate_model_request_input_tokens(request: ModelRequest) -> int:
    """估算单次模型调用的输入 token（对话 + system + tools）。"""
    return _estimate_input_tokens_with_method(request)
