"""加载 fixture → 调用 SummarizationOffloadMiddleware.before_model → 压缩后 messages。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, convert_to_messages
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from agent.middlewares.context_metrics import get_agent_token_counter, resolve_context_max_tokens
from agent.middlewares.summary_offload_middleware import SummarizationOffloadMiddleware
from config.env import ModelConfig
from llm import get_llm


def _require_summarization_enabled() -> None:
    if not ModelConfig.summarization_enabled:
        raise RuntimeError(
            "压缩评测需要启用 summarization（config.yaml summarization.enabled 或 "
            "SUMMARIZATION_ENABLED=true）"
        )


def parse_fixture_messages(raw: List[Dict[str, Any]]) -> List[AnyMessage]:
    lc_payload: List[Dict[str, Any]] = []
    for msg in raw:
        mtype = str(msg.get("type") or "")
        content = msg.get("content", "")
        if mtype == "human":
            lc_payload.append({"role": "user", "content": content})
        elif mtype in ("ai", "assistant"):
            lc_payload.append({"role": "assistant", "content": content})
        elif mtype == "system":
            lc_payload.append({"role": "system", "content": content})
        elif mtype == "tool":
            lc_payload.append(
                {
                    "role": "tool",
                    "content": content,
                    "tool_call_id": msg.get("tool_call_id") or "call_tool",
                    "name": msg.get("name") or "tool",
                }
            )
        else:
            raise ValueError(f"未知 message type: {mtype}")
    return convert_to_messages(lc_payload)


def build_eval_middleware(compress_options: Optional[Dict[str, Any]] = None) -> SummarizationOffloadMiddleware:
    _require_summarization_enabled()
    options = dict(compress_options or {})
    force = bool(options.get("force", True))
    keep_n = int(
        options.get("summarization_messages_to_keep") or ModelConfig.summarization_messages_to_keep
    )

    model = get_llm(purpose="summarization")
    max_input = resolve_context_max_tokens()
    if not getattr(model, "profile", None):
        model.profile = {"max_input_tokens": max_input}

    if force:
        trigger: tuple[str, float | int] = ("tokens", 1)
    elif ModelConfig.summarization_trigger_tokens > 0:
        trigger = ("tokens", ModelConfig.summarization_trigger_tokens)
    else:
        trigger = ("fraction", ModelConfig.summarization_trigger_fraction)

    return SummarizationOffloadMiddleware(
        model,
        trigger=trigger,
        keep=("messages", keep_n),
        token_counter=get_agent_token_counter(),
        tool_offload_threshold=ModelConfig.summarization_tool_offload_threshold,
        max_retention_ratio=ModelConfig.summarization_max_retention_ratio,
    )


def _apply_message_update(original: List[AnyMessage], update: Dict[str, Any]) -> List[AnyMessage]:
    patch = update.get("messages")
    if not patch:
        return list(original)

    first = patch[0]
    if getattr(first, "id", None) == REMOVE_ALL_MESSAGES:
        return list(patch[1:])

    by_id = {m.id: m for m in patch if getattr(m, "id", None)}
    if not by_id:
        return list(original)

    merged: List[AnyMessage] = []
    for msg in original:
        mid = getattr(msg, "id", None)
        merged.append(by_id[mid] if mid in by_id else msg)
    return merged


def _extract_summary_text(messages: List[AnyMessage]) -> str:
    for msg in messages:
        if isinstance(msg, (HumanMessage, AIMessage, SystemMessage)):
            content = msg.content
            if isinstance(content, str) and "[Conversation Summary]" in content:
                return content
            if isinstance(content, str) and len(content) > 200 and "summary" in content.lower():
                return content
    return ""


def compress_fixture_messages(
    messages: List[AnyMessage],
    *,
    compress_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    middleware = build_eval_middleware(compress_options)
    state: AgentState = {"messages": list(messages)}
    runtime = Runtime(context=SimpleNamespace(backend=None))

    token_counter = middleware.token_counter
    pre_tokens = token_counter(messages)
    pre_count = len(messages)

    update = middleware.before_model(state, runtime)
    compressed = _apply_message_update(messages, update or {})
    if update:
        state = {**state, **update}
        if "messages" in update and getattr(update["messages"][0], "id", None) == REMOVE_ALL_MESSAGES:
            compressed = list(update["messages"][1:])

    post_tokens = token_counter(compressed)
    post_count = len(compressed)
    ratio = (1.0 - post_tokens / pre_tokens) if pre_tokens > 0 else 0.0

    return {
        "compressed_messages": compressed,
        "summary_text": _extract_summary_text(compressed),
        "compressed": update is not None,
        "pre_tokens": pre_tokens,
        "post_tokens": post_tokens,
        "compression_ratio": round(ratio, 4),
        "pre_message_count": pre_count,
        "post_message_count": post_count,
    }
