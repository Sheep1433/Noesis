"""BG completion notify middleware — 主 Agent 在 run 内即时感知后台任务终态。

后台子 Agent 到达终态时，若主 Agent 的 run 仍在执行（模型 start 后继续
干活），本中间件在**下一次模型调用边界**把未送达的 ``[系统通知]`` 追加
为 HumanMessage——模型同一轮内即可 check_task 收果，无需等下一轮对话。
run 已结束则由 ``exec_query`` 的下一轮注入兜底（同一份通知注册表，
delivered 标记保证不重复注入）。
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
from langchain_core.messages import HumanMessage

from noesis.agents.subagents.notifications import take_undelivered, render_block

if TYPE_CHECKING:
    from collections.abc import Awaitable


class BgNotifyMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """模型调用边界注入后台任务终态通知（仅主 Agent 栈挂载）。"""

    def __init__(self, *, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id

    def _injected_messages(self) -> list[HumanMessage]:
        notices = take_undelivered(self._session_id)
        if not notices:
            return []
        block = render_block(notices)
        return [HumanMessage(content=block)] if block else []

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT], ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        messages = self._injected_messages()
        if messages:
            request = request.override(messages=[*request.messages, *messages])
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT], Awaitable[ModelResponse[ResponseT]]]],
    ) -> ModelResponse[ResponseT]:
        messages = self._injected_messages()
        if messages:
            request = request.override(messages=[*request.messages, *messages])
        return await handler(request)


__all__ = ["BgNotifyMiddleware"]
