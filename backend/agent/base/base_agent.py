from collections.abc import AsyncGenerator

from langchain_core.messages import convert_to_messages
from langgraph.types import Command

from agent.hitl.stream import (
    _tool_calls_from_model_end,
    build_hitl_required_event,
    extract_interrupt_payload,
)
from common.logging import logger
from config.checkpointer import get_checkpointer
from domain.observability.langfuse import langfuse_tracing_enabled, merge_langfuse_runnable_config

DEFAULT_RECURSION_LIMIT = 200


def _format_agent_stream_error(exc: BaseException) -> str:
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


class BaseAgent:
    """
    Agent 基类，定义通用接口和方法
    使用 LangChain astream_events 流式输出；直接产出 LangGraph 事件 dict（见 langgraph_sse_bridge）。
    """

    def __init__(self):
        self.running_tasks = {}

    @property
    def checkpointer(self):
        return get_checkpointer()

    async def cancel_task(self, task_id: str) -> bool:
        """取消指定的任务"""
        if task_id in self.running_tasks:
            self.running_tasks[task_id]["cancelled"] = True
            logger.info(f"BaseAgent.cancel_task 已标记取消 task_id={task_id}")
            return True
        logger.info(f"BaseAgent.cancel_task 无运行中任务 task_id={task_id}")
        return False

    def get_running_tasks(self):
        """获取当前运行中的任务列表"""
        return list(self.running_tasks.keys())

    async def _stream_agent_response(
        self, agent, stream_args, task_id: str, _message_id: str
    ) -> AsyncGenerator[dict, None]:
        """
        消费 agent.astream_events，原样 yield LangGraph/LangChain 事件 dict；
        结束时 yield __tw_finish__；取消 / 异常时 yield 控制哨兵（由 langgraph_sse_bridge 转 SSE）。
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

        try:
            async for event in agent.astream_events(
                stream_input,
                config=agent_config,
            ):
                if self.running_tasks.get(task_id, {}).get("cancelled"):
                    logger.info(
                        f"astream_events 因 cancel_task 中断 task_id={task_id} message_id={_message_id}"
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
                        message_id=_message_id,
                        tool_calls=last_tool_calls,
                    )
                    yield {"type": "__tw_finish__", "finish_reason": "hitl_pending"}
                    hitl_pending = True
                    break

                yield event

            if not hitl_pending:
                yield {"type": "__tw_finish__", "finish_reason": "stop"}

        except Exception as e:
            logger.exception(
                f"_stream_agent_response 异常 task_id={task_id} message_id={_message_id}"
            )
            yield {"type": "__tw_error__", "content": _format_agent_stream_error(e)}
            yield {"type": "__tw_finish__", "finish_reason": "error"}
