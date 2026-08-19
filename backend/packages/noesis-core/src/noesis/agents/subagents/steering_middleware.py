"""Steering middleware — 后台子 Agent 的中途指令注入。

在 task-worker 栈的模型调用边界 drain 该 task（thread_id = task_id）的
待注入调整指令，追加为 HumanMessage；注入即消费，不重复出现在后续轮次。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import AnyMessage, HumanMessage

from noesis.agents.subagents import steering

if TYPE_CHECKING:
    from collections.abc import Awaitable


def _thread_id(request: ModelRequest[ContextT]) -> str:
    config = getattr(request.runtime, "config", None)
    if isinstance(config, dict):
        thread_id = (config.get("configurable") or {}).get("thread_id")
        if thread_id:
            return str(thread_id)
    return ""


class SteeringMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """把 steering 队列中的用户调整指令注入下一次模型调用。"""

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT], ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        messages = self._injected_messages(request)
        if messages:
            request = request.override(messages=[*request.messages, *messages])
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT], Awaitable[ModelResponse[ResponseT]]]],
    ) -> ModelResponse[ResponseT]:
        messages = self._injected_messages(request)
        if messages:
            request = request.override(messages=[*request.messages, *messages])
        return await handler(request)

    @staticmethod
    def _injected_messages(request: ModelRequest[ContextT]) -> list[AnyMessage]:  # noqa: ANN401
        task_id = _thread_id(request)
        if not task_id:
            return []
        instructions = steering.drain(task_id)
        return [
            HumanMessage(content=f"[用户策略调整] {text}")
            for text in instructions
        ]

__all__ = ["SteeringMiddleware"]
