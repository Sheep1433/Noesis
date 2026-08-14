"""加载 fixture → 调用 CompactionMiddleware → 压缩后 messages。

离线评测使用与线上相同的 ``noesis.agents.middlewares.CompactionMiddleware``
（spec：离线评测使用同一 factory 入口与 middleware 参数）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, convert_to_messages

from noesis.agents.middlewares import CompactionMiddleware, CompactionThresholds
from noesis.llm.model_limits import resolve_context_max_tokens
from noesis.config.env import ModelConfig
from noesis.llm import get_llm


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


def _approx_token_counter(messages: List[AnyMessage]) -> int:
    return sum(len(repr(m.content)) for m in messages) // 4


def _build_summarize(model):
    def _summarize(messages: List[AnyMessage]) -> str:
        from langchain_core.messages import get_buffer_string

        prompt = (
            "Summarise the following conversation, preserving: user goals, key "
            "technical decisions, files/code, errors and fixes, rejected "
            "approaches, all user requirements, pending tasks, current work, "
            "and the next step.\n\n"
            f"{get_buffer_string(messages)}"
        )
        return str(model.invoke([HumanMessage(content=prompt)]).content or "")
    return _summarize


def build_eval_middleware(compress_options: Optional[Dict[str, Any]] = None) -> CompactionMiddleware:
    _require_summarization_enabled()
    options = dict(compress_options or {})
    force = bool(options.get("force", True))
    keep_n = int(
        options.get("summarization_messages_to_keep") or ModelConfig.summarization_messages_to_keep
    )

    model = get_llm(purpose="summarization")
    max_input = resolve_context_max_tokens() or 128_000
    reserve = int(getattr(ModelConfig, "summarization_output_reserve", 4_000))

    if force:
        auto_at = 1  # force compaction on first call
    elif ModelConfig.summarization_trigger_tokens > 0:
        auto_at = ModelConfig.summarization_trigger_tokens
    else:
        auto_at = int(max_input * getattr(ModelConfig, "summarization_trigger_fraction", 0.75))

    reserve = min(reserve, max_input - 2)
    effective_limit = max_input - reserve
    thresholds = CompactionThresholds(
        model_input_limit=max_input,
        summary_output_reserve=reserve,
        transient_request_buffer=max(0, effective_limit - auto_at),
    )
    return CompactionMiddleware(
        token_counter=_approx_token_counter,
        summarize=_build_summarize(model),
        thresholds=thresholds,
        keep_messages=keep_n,
    )


def _extract_summary_text(messages: List[AnyMessage]) -> str:
    for msg in messages:
        if isinstance(msg, (HumanMessage, AIMessage, SystemMessage)):
            content = msg.content
            if msg.additional_kwargs.get("lc_source") == "summarization" and isinstance(content, str):
                return content
            if isinstance(content, str) and "[conversation summary]" in content:
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

    pre_tokens = _approx_token_counter(messages)
    pre_count = len(messages)

    request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=list(messages),
        system_message=SystemMessage(content="eval"),
        state={"messages": list(messages)},
    )

    effective: list[AnyMessage] = []

    def handler(req: ModelRequest) -> ModelResponse:
        effective.extend(req.messages)
        return ModelResponse(result=[AIMessage(content="eval complete")])

    middleware.wrap_model_call(request, handler)
    compressed = effective or list(request.messages)

    post_tokens = _approx_token_counter(compressed)
    post_count = len(compressed)
    ratio = (1.0 - post_tokens / pre_tokens) if pre_tokens > 0 else 0.0

    return {
        "compressed_messages": compressed,
        "summary_text": _extract_summary_text(compressed),
        "compressed": len(compressed) != pre_count,
        "pre_tokens": pre_tokens,
        "post_tokens": post_tokens,
        "compression_ratio": round(ratio, 4),
        "pre_message_count": pre_count,
        "post_message_count": post_count,
    }
