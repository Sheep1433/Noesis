"""Shared agent event stream (LangGraph astream_events → dicts).

Platform delivery and evals both consume this; the core runtime owns HITL shaping.
"""

from __future__ import annotations

import time

from collections.abc import AsyncGenerator, Callable
from typing import Any

from langchain_core.messages import convert_to_messages
from langgraph.types import Command

from noesis.runtime.logging import logger
from noesis.runtime.deps import langfuse_tracing_enabled, merge_langfuse_runnable_config
from noesis.agents.middlewares.compaction_middleware import COMPACTION_SUMMARY_TAG
from noesis.runtime.hitl import (
    _tool_calls_from_model_end,
    build_hitl_required_event,
    extract_interrupt_payload,
)

DEFAULT_RECURSION_LIMIT = 9999


def format_agent_stream_error(exc: BaseException) -> str:
    """OpenAI 等客户端常把细节放在 __cause__，拼成可读一句给前端 SSE。"""
    head = str(exc).strip()
    cause = getattr(exc, "__cause__", None)
    tail = str(cause).strip() if cause else ""
    if head and tail:
        combined = f"{head}（{tail}）"
    elif head:
        combined = head
    elif tail:
        combined = tail
    else:
        combined = exc.__class__.__name__

    lower = combined.lower()
    if "recursion limit" in lower or exc.__class__.__name__ == "GraphRecursionError":
        return "已达到最大处理步数，任务已自动停止。"
    return combined


async def stream_agent_events(
    agent: Any,
    stream_args: dict[str, Any],
    *,
    task_id: str,
    message_id: str,
    is_cancelled: Callable[[], bool] | None = None,
) -> AsyncGenerator[dict, None]:
    """消费 agent.astream_events，原样 yield LangGraph/LangChain 事件 dict。

    结束时 yield ``__tw_finish__``；取消 / 异常时 yield 控制哨兵。
    HITL interrupt：转为 ``hitl-required`` + ``finish_reason=hitl_pending``。
    """
    raw_input = stream_args.get("input", {})
    if isinstance(raw_input, Command):
        stream_input: object = raw_input
    else:
        stream_input = dict(raw_input or {})
        input_messages = stream_input.get("messages", [])
        if isinstance(input_messages, list):
            stream_input["messages"] = convert_to_messages(input_messages)

    original_config = stream_args.get("config", {})
    recursion_limit = original_config.get("recursion_limit", DEFAULT_RECURSION_LIMIT)
    configurable = original_config.get("configurable", {})
    agent_config: dict = {
        "configurable": configurable,
        "recursion_limit": recursion_limit,
    }
    for key in ("callbacks", "metadata", "tags", "run_name", "run_id"):
        if key in original_config and original_config[key] is not None:
            agent_config[key] = original_config[key]

    agent_config = merge_langfuse_runnable_config(
        agent_config,
        langfuse_session_id=stream_args.get("langfuse_session_id"),
        qa_type=stream_args.get("qa_type"),
        enabled=langfuse_tracing_enabled(),
    )

    session_id = str(stream_args.get("langfuse_session_id") or task_id or "")
    last_tool_calls: list[dict] = []
    hitl_pending = False

    callbacks = agent_config.get("callbacks")
    if callbacks is None:
        agent_config["callbacks"] = []
    elif isinstance(callbacks, list):
        agent_config["callbacks"] = [*callbacks]
    else:
        agent_config["callbacks"] = [callbacks]

    try:
        _last_event_at = time.monotonic()
        async for event in agent.astream_events(
            stream_input,
            config=agent_config,
        ):
            # 压缩摘要生成是图内基础设施调用（factory 打 tag）：其模型事件
            # 不得进入消息投影——否则摘要 JSON 被当成正文 text part 上屏，
            # usage/步数也混入该调用
            if COMPACTION_SUMMARY_TAG in (event.get("tags") or []):
                continue
            _last_event_at = time.monotonic()
            if is_cancelled and is_cancelled():
                logger.info(
                    f"astream_events 因 cancel_task 中断 task_id={task_id} message_id={message_id}"
                )
                yield {"type": "__tw_abort__"}
                break

            model_tcs = _tool_calls_from_model_end(event)
            if model_tcs:
                last_tool_calls = model_tcs

            interrupt = extract_interrupt_payload(event)
            if interrupt is not None:
                interrupt_id, hitl_value = interrupt
                logger.info(
                    f"HITL interrupt task_id={task_id} interrupt_id={interrupt_id}"
                )
                yield build_hitl_required_event(
                    interrupt_id=interrupt_id,
                    hitl_value=hitl_value,
                    session_id=session_id,
                    message_id=message_id,
                    tool_calls=last_tool_calls,
                )
                yield {"type": "__tw_finish__", "finish_reason": "hitl_pending"}
                hitl_pending = True
                break

            yield event

        if not hitl_pending:
            # astream_events 耗尽耗时（最后一个事件 → 循环退出）：图结束阶段的
            # 终态 checkpoint 写入 / middleware 收尾钩子都在这段，无事件帧可观察。
            # 慢于此阈值说明收尾被拖长（对比 SSE 尾部空转现象）。
            drain_seconds = time.monotonic() - _last_event_at
            if drain_seconds > 1.0:
                logger.info(
                    f"astream_drain_slow task_id={task_id} message_id={message_id} "
                    f"drain_seconds={drain_seconds:.2f}"
                )
            yield {"type": "__tw_finish__", "finish_reason": "stop"}

    except Exception as e:
        logger.exception(
            f"stream_agent_events 异常 task_id={task_id} message_id={message_id}"
        )
        yield {"type": "__tw_error__", "content": format_agent_stream_error(e)}
        yield {"type": "__tw_finish__", "finish_reason": "error"}
