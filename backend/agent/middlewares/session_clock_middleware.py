"""在模型调用前注入会话参考时间（仅作用于当次 ModelRequest，不写入 checkpointer）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from typing_extensions import override

_CLOCK_KWARG = "noesis_session_clock"
_WEEKDAY_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_DEFAULT_TZ = "Asia/Shanghai"


def is_session_clock_message(message: object) -> bool:
    """判断是否为会话时钟提示消息（供展示层过滤时复用）。"""
    if not isinstance(message, HumanMessage):
        return False
    return bool((message.additional_kwargs or {}).get(_CLOCK_KWARG))


class SessionClockMiddleware(AgentMiddleware[AgentState]):
    """每次模型调用前，在最后一条用户消息前临时附加当前参考时间。"""

    def __init__(self, *, timezone_name: str = _DEFAULT_TZ) -> None:
        super().__init__()
        self._tz = ZoneInfo(timezone_name)

    def _render_clock_block(self) -> str:
        now = datetime.now(self._tz)
        weekday = _WEEKDAY_ZH[now.weekday()]
        return (
            "<session_context>\n"
            f"参考时间：{now:%Y-%m-%d %H:%M:%S}（{weekday}，{self._tz.key}）\n"
            "涉及日期、截止、排期等问题请以此为准。\n"
            "</session_context>"
        )

    def _patch_messages(self, messages: list) -> list | None:
        if not messages:
            return None

        last_human_idx = None
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if getattr(msg, "type", None) != "human":
                continue
            if is_session_clock_message(msg):
                return None
            last_human_idx = idx
            break

        if last_human_idx is None:
            return None

        clock_msg = HumanMessage(
            content=self._render_clock_block(),
            additional_kwargs={_CLOCK_KWARG: True},
        )
        patched = list(messages)
        patched.insert(last_human_idx, clock_msg)
        return patched

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        patched = self._patch_messages(list(request.messages))
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        patched = self._patch_messages(list(request.messages))
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)
