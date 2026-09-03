"""Contracts for prefix-cache-safe dynamic context injection.

设计契约（参照 Claude Code prependUserContext / date_change）：
- 头部块会话首轮冻结、之后逐字节不变（跨轮公共前缀不被分叉）；
- 跨日不改写冻结块，尾部追加纠正声明；
- 附件集合变化时尾部声明，不属于冻结块；
- 同日多轮：两次投影的头部消息完全一致（含位置）。
"""

from __future__ import annotations

import asyncio
from typing import get_args, get_type_hints

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, PrivateStateAttr
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from noesis.agents.middlewares.dynamic_context_middleware import (
    DynamicContextBlock,
    DynamicContextMiddleware,
    DynamicContextState,
    render_head_block,
)


def _request(state: dict, system_prompt: str = "static") -> ModelRequest:
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[HumanMessage(content="hi")],
        system_message=SystemMessage(content=system_prompt),
        state=state,
    )


def _project(middleware: DynamicContextMiddleware, state: dict) -> list:
    seen: list[ModelRequest] = []
    middleware.wrap_model_call(_request(state), lambda request: seen.append(request))  # type: ignore[arg-type,return-value]
    return list(seen[0].messages)


def _head_text(messages: list) -> str:
    assert isinstance(messages[0], SystemMessage)
    return str(messages[0].content)


def test_state_schema_marks_rendered_block_private() -> None:
    assert DynamicContextMiddleware.state_schema is DynamicContextState
    hint = get_type_hints(DynamicContextState, include_extras=True)["dynamic_context_block"]
    assert PrivateStateAttr in get_args(get_args(hint)[0])


def test_langchain_graph_runs_hook_and_omits_private_block_from_output() -> None:
    calls = 0

    def provider() -> DynamicContextBlock:
        nonlocal calls
        calls += 1
        return DynamicContextBlock("2026-09-02", "UTC")

    agent = create_agent(
        model=FakeListChatModel(responses=["ok"]),
        tools=[],
        middleware=[DynamicContextMiddleware(context_provider=provider)],
    )

    result = agent.invoke({"messages": [HumanMessage(content="hello")]})

    assert calls == 1
    assert "dynamic_context_block" not in result


def test_head_block_frozen_across_runs_and_same_day_has_no_tail() -> None:
    """同日第二轮：头部块与首轮逐字节一致（前缀缓存不被分叉），无尾部纠正。"""
    def provider() -> DynamicContextBlock:
        return DynamicContextBlock("2026-09-02", "UTC", workspace="ws-1")

    middleware = DynamicContextMiddleware(context_provider=provider)
    first = middleware.before_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]
    assert first is not None
    state1 = {**(first or {})}
    state2 = {**(first or {}), "messages": [HumanMessage("second turn")]}

    second = middleware.before_agent(state2, runtime=None)  # type: ignore[arg-type]
    assert second is not None

    messages1 = _project(middleware, state1)
    messages2 = _project(middleware, {**state2, **(second or {})})
    assert _head_text(messages1) == _head_text(messages2)
    assert "Today's date is 2026-09-02 (UTC)." in _head_text(messages1)
    assert "Workspace: ws-1" in _head_text(messages1)
    # 同日无纠正、无附件声明：投影只多出头部块一条
    assert len(messages2) == 2  # head + hi（second turn 的输入在 state，不在本投影断言里）
    assert not any("date is now" in str(m.content) for m in messages2)


def test_date_rollover_keeps_frozen_head_and_appends_tail_correction() -> None:
    """跨日：冻结块保留过期日期，尾部追加新日期声明（不改写头部）。"""
    dates = iter(("2026-09-02", "2026-09-03"))
    middleware = DynamicContextMiddleware(
        context_provider=lambda: DynamicContextBlock(next(dates), "UTC", workspace="ws"),
    )
    first = middleware.before_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]
    assert first is not None
    second = middleware.before_agent({**(first or {})}, runtime=None)  # type: ignore[arg-type]
    assert second is not None
    assert second["dynamic_context_tail"] == "Today's date is now 2026-09-03 (UTC)."
    # 冻结块不被跨日改写（update 里不出现该键 = 保持 checkpoint 中的旧值）
    assert "dynamic_context_block" not in second

    messages = _project(middleware, {**(first or {}), **(second or {})})
    assert "Today's date is 2026-09-02" in _head_text(messages)
    tail = messages[-1]
    assert isinstance(tail, SystemMessage)
    assert "Today's date is now 2026-09-03 (UTC)." in str(tail.content)


def test_attachment_change_appends_tail_notice_once() -> None:
    """附件集合变化时尾部声明一次；集合不变时不再重复。"""
    attachments = iter((("plan.md",), ("plan.md",), ("plan.md", "data.csv")))
    middleware = DynamicContextMiddleware(
        context_provider=lambda: DynamicContextBlock("2026-09-02", "UTC", attachments=next(attachments)),
    )
    first = middleware.before_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]
    assert first is not None
    assert "Attachments available: plan.md." in first["dynamic_context_tail"]

    state = {**(first or {})}
    second = middleware.before_agent(state, runtime=None)  # type: ignore[arg-type]
    assert second is not None
    assert second["dynamic_context_tail"] == ""

    third = middleware.before_agent({**state, **(second or {})}, runtime=None)  # type: ignore[arg-type]
    assert third is not None
    assert "Attachments available: plan.md, data.csv." in third["dynamic_context_tail"]


def test_no_provider_clears_state_and_injects_nothing() -> None:
    middleware = DynamicContextMiddleware()
    update = middleware.before_agent(
        {"messages": [], "dynamic_context_block": "stale", "dynamic_context_tail": "stale"},
        runtime=None,  # type: ignore[arg-type]
    )
    assert update == {"dynamic_context_block": "", "dynamic_context_tail": ""}
    assert _project(middleware, {"messages": [], **update}) == [HumanMessage(content="hi")]


def test_render_head_block_layout() -> None:
    text = render_head_block(DynamicContextBlock("2026-09-02", "Asia/Shanghai"))
    assert "Today's date is 2026-09-02 (Asia/Shanghai)." in text
    assert "Workspace:" not in text
    assert "Attachments" not in text
    assert "Session" not in text

    with_ws = render_head_block(DynamicContextBlock("2026-09-02", "UTC", workspace="/workspace"))
    assert "Workspace: /workspace" in with_ws


def test_sync_hook_rejects_async_provider() -> None:
    async def provider() -> DynamicContextBlock:
        return DynamicContextBlock("2026-09-02", "UTC")

    middleware = DynamicContextMiddleware(context_provider=provider)
    with pytest.raises(TypeError, match="async context_provider"):
        middleware.before_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]


def test_async_hook_resolves_provider_once_for_the_run() -> None:
    calls = 0

    async def provider() -> DynamicContextBlock:
        nonlocal calls
        calls += 1
        return DynamicContextBlock("2026-09-02", "UTC", workspace="async-ws")

    middleware = DynamicContextMiddleware(context_provider=provider)

    async def scenario() -> tuple[dict | None, list]:
        update = await middleware.abefore_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]

        async def handler(request: ModelRequest) -> list:
            return list(request.messages)

        messages = await middleware.awrap_model_call(
            _request({"messages": [], **(update or {})}),
            handler,  # type: ignore[arg-type]
        )
        return update, messages

    update, messages = asyncio.run(scenario())
    assert update is not None
    assert calls == 1
    assert "Workspace: async-ws" in _head_text(messages)
