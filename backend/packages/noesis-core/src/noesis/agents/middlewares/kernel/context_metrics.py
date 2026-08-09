"""Shared context window metrics for summarization and UI display."""

from __future__ import annotations

import json
from typing import Any, Optional

from langchain.agents.middleware.summarization import TokenCounter, count_tokens_approximately
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.utils.function_calling import convert_to_openai_tool

from noesis.llm.model_limits import resolve_context_max_tokens

#: 顶层分类键，顺序固定，供 UI 与聚合一致消费
BREAKDOWN_KEYS: tuple[str, ...] = (
    "system",
    "conversation",
    "tool_results",
    "tool_definitions",
    "other",
)

#: 默认调用方；真实 caller 由 attribution 中间件按 run 上下文传入
DEFAULT_CONTEXT_CALLER = "lead_agent"

#: 计数方法标识
METHOD_MODEL_TOKENIZER = "model_tokenizer"
METHOD_APPROXIMATE = "approximate"


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


def _classify_request_messages(
    messages: list[Any],
    system_message: SystemMessage | None,
) -> tuple[list[Any], list[Any], list[Any]]:
    """拆分最终请求消息为 system / conversation / tool_results 三组。

    system 组单独取出 system_message；conversation 组为对话消息；tool_results 组为
    ToolMessage。分类只面向 token 估算，不改写请求内容。
    """
    conversation: list[Any] = []
    tool_results: list[Any] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_results.append(msg)
        else:
            conversation.append(msg)
    system = [system_message] if system_message is not None else []
    return system, conversation, tool_results


def build_request_breakdown(
    messages: list[Any],
    system_message: SystemMessage | None,
    tools: list[Any],
) -> dict[str, int]:
    """纯函数：用 approximate counter 估算顶层分类 token。

    breakdown 各分类使用同一条 approximate 计数路径；``other`` 由调用方根据
    ``current_tokens`` 与分类之和的差值填充，吸收序列化/framing 或 model tokenizer
    与 approximate 之间的差值。返回的 dict 不含 ``other``，由调用方补齐。
    """
    counter = get_agent_token_counter()
    system_msgs, conversation, tool_results = _classify_request_messages(messages, system_message)
    return {
        "system": int(counter(system_msgs)) if system_msgs else 0,
        "conversation": int(counter(conversation)),
        "tool_results": int(counter(tool_results)) if tool_results else 0,
        "tool_definitions": _estimate_tools_tokens_approx(tools),
        "other": 0,
    }


def _estimate_input_tokens_with_method(request: ModelRequest) -> tuple[int, str]:
    """估算输入 token 并返回所用计数方法。

    优先使用模型公开 tokenizer（``model.get_num_tokens``）；不可用时回退到
    LangChain approximate counter。返回值用于 context snapshot 的 ``counting_method``
    与 ``current_tokens``，保证两者口径一致可追溯。
    """
    messages = _messages_with_system(list(request.messages), request.system_message)
    tools = list(request.tools or [])
    get_num_tokens = getattr(request.model, "get_num_tokens", None)
    if callable(get_num_tokens):
        try:
            return (
                int(get_num_tokens(_serialize_request_payload(messages, tools))),
                METHOD_MODEL_TOKENIZER,
            )
        except (ImportError, RuntimeError, ValueError, TypeError):
            # Some test/fake models expose the LangChain method but rely on an
            # optional tokenizer package.  The shared approximate counter is a
            # safe fallback for occupancy; it is never reported as provider usage.
            pass
    return (
        int(get_agent_token_counter()(messages)) + _estimate_tools_tokens_approx(tools),
        METHOD_APPROXIMATE,
    )


def estimate_model_request_input_tokens(request: ModelRequest) -> int:
    """估算单次模型调用的输入 token（对话 + system + tools）。"""
    return _estimate_input_tokens_with_method(request)[0]


def compute_used_percentage(current_tokens: int, max_tokens: int) -> int:
    """占用百分比；有占用但四舍五入为 0 时显示 1%，避免圆环长期为 0%。"""
    if max_tokens <= 0 or current_tokens <= 0:
        return 0
    pct = round(current_tokens / max_tokens * 100)
    if pct == 0:
        return 1
    return min(100, pct)


def _finalize_breakdown(breakdown: dict[str, int], current_tokens: int) -> dict[str, int]:
    """用 current_tokens 与分类之和的差值填充 other，保证 breakdown 之和 == current_tokens。"""
    classified = sum(breakdown.get(key, 0) for key in BREAKDOWN_KEYS if key != "other")
    other = current_tokens - classified
    breakdown["other"] = other if other > 0 else 0
    return breakdown


def build_context_snapshot(
    messages: list[Any],
    *,
    model_id: Optional[str] = None,
) -> dict[str, Any]:
    """仅基于消息列表的粗估（摘要触发等内部逻辑使用）。"""
    current_tokens = int(get_agent_token_counter()(messages))
    max_tokens = resolve_context_max_tokens(model_id)
    used_percentage = compute_used_percentage(current_tokens, max_tokens)
    return {
        "current_tokens": current_tokens,
        "max_tokens": max_tokens,
        "used_percentage": used_percentage,
    }


def build_context_snapshot_from_request(
    request: ModelRequest,
    *,
    model_id: Optional[str] = None,
    caller: str = DEFAULT_CONTEXT_CALLER,
) -> dict[str, Any]:
    """Composer 上下文指示器：对齐即将发往模型的有效输入规模。

    向后兼容保留 ``current_tokens``、``max_tokens``、``used_percentage``；新增
    ``breakdown``（system/conversation/tool_results/tool_definitions/other，和等于
    ``current_tokens``）、``sources``（provenance 驱动的来源细分，由注入方填充）、
    ``estimated``（始终为 True，本地估算）、``counting_method`` 与 ``caller``。
    """
    current_tokens, method = _estimate_input_tokens_with_method(request)
    max_tokens = resolve_context_max_tokens(model_id)
    used_percentage = compute_used_percentage(current_tokens, max_tokens)
    breakdown = _finalize_breakdown(
        build_request_breakdown(
            list(request.messages),
            request.system_message,
            list(request.tools or []),
        ),
        current_tokens,
    )
    # sources 由 Skills/memory/RAG/attachments 注入方通过 request-scoped
    # provenance 标记（task 2.x）；缺失标记时为空，对应内容留在父分类。
    from noesis.runtime.context_provenance import current_context_provenance

    provenance = current_context_provenance()
    sources = provenance.snapshot() if provenance is not None else {}
    return {
        "current_tokens": current_tokens,
        "max_tokens": max_tokens,
        "used_percentage": used_percentage,
        "estimated": True,
        "counting_method": method,
        "breakdown": breakdown,
        "sources": sources,
        "caller": caller,
    }
