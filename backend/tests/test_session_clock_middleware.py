"""SessionClockMiddleware 单测。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.middlewares.session_clock_middleware import (
    SessionClockMiddleware,
    is_session_clock_message,
)


def test_is_session_clock_message() -> None:
    clock = HumanMessage(content="x", additional_kwargs={"noesis_session_clock": True})
    user = HumanMessage(content="hello")
    assert is_session_clock_message(clock)
    assert not is_session_clock_message(user)
    assert not is_session_clock_message(AIMessage(content="hi"))


def test_patch_inserts_clock_before_last_human() -> None:
    mw = SessionClockMiddleware(timezone_name="Asia/Shanghai")
    user = HumanMessage(content="今天星期几？")
    patched = mw._patch_messages([HumanMessage(content="上一轮"), AIMessage(content="回复"), user])
    assert patched is not None
    assert len(patched) == 4
    assert is_session_clock_message(patched[2])
    assert patched[3] is user
    assert "<session_context>" in patched[2].content


def test_patch_skips_when_last_human_is_clock() -> None:
    mw = SessionClockMiddleware()
    clock = HumanMessage(content="clock", additional_kwargs={"noesis_session_clock": True})
    assert mw._patch_messages([AIMessage(content="ok"), clock]) is None


def test_wrap_model_call_forwards_patched_messages() -> None:
    mw = SessionClockMiddleware()
    user = HumanMessage(content="你好")
    request = MagicMock()
    request.messages = [user]
    captured: dict = {}

    def handler(req):
        captured["messages"] = list(req.messages)
        return MagicMock()

    request.override = lambda *, messages: MagicMock(messages=messages)

    mw.wrap_model_call(request, handler)

    msgs = captured["messages"]
    assert len(msgs) == 2
    assert is_session_clock_message(msgs[0])
    assert msgs[1] is user


@pytest.mark.asyncio
async def test_awrap_model_call_forwards_patched_messages() -> None:
    mw = SessionClockMiddleware()
    user = HumanMessage(content="你好")
    request = MagicMock()
    request.messages = [user]
    captured: dict = {}

    async def handler(req):
        captured["messages"] = list(req.messages)
        return MagicMock()

    request.override = lambda *, messages: MagicMock(messages=messages)

    await mw.awrap_model_call(request, handler)

    msgs = captured["messages"]
    assert len(msgs) == 2
    assert is_session_clock_message(msgs[0])


def test_render_clock_uses_configured_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    mw = SessionClockMiddleware(timezone_name="UTC")
    fixed = datetime(2026, 6, 10, 8, 30, 0, tzinfo=ZoneInfo("UTC"))

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr("agent.middlewares.session_clock_middleware.datetime", _FixedDatetime)
    block = mw._render_clock_block()
    assert "2026-06-10 08:30:00" in block
    assert "周三" in block
    assert "UTC" in block
