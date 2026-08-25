"""Contracts for run-stable dynamic context injection."""

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
    render_dynamic_block,
)


def _request(state: dict, system_prompt: str = "static") -> ModelRequest:
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[],
        system_message=SystemMessage(content=system_prompt),
        state=state,
    )


def _invoke(middleware: DynamicContextMiddleware, state: dict) -> str:
    seen: list[ModelRequest] = []
    middleware.wrap_model_call(_request(state), lambda request: seen.append(request))  # type: ignore[arg-type,return-value]
    return "\n".join([
        seen[0].system_message.text,
        *(str(message.content) for message in seen[0].messages),
    ])


def test_state_schema_marks_rendered_block_private() -> None:
    assert DynamicContextMiddleware.state_schema is DynamicContextState
    hint = get_type_hints(DynamicContextState, include_extras=True)["dynamic_context_block"]
    assert PrivateStateAttr in get_args(get_args(hint)[0])


def test_langchain_graph_runs_hook_and_omits_private_block_from_output() -> None:
    calls = 0

    def provider() -> DynamicContextBlock:
        nonlocal calls
        calls += 1
        return DynamicContextBlock("graph-turn", "UTC")

    agent = create_agent(
        model=FakeListChatModel(responses=["ok"]),
        tools=[],
        middleware=[DynamicContextMiddleware(context_provider=provider)],
    )

    result = agent.invoke({"messages": [HumanMessage(content="hello")]})

    assert calls == 1
    assert "dynamic_context_block" not in result


def test_before_agent_resolves_once_and_model_calls_reuse_same_block() -> None:
    calls = 0

    def provider() -> DynamicContextBlock:
        nonlocal calls
        calls += 1
        return DynamicContextBlock(
            current_time=f"turn-{calls}",
            timezone="UTC",
            workspace="ws-1",
            session_id="session-1",
            attachments=("plan.md", "data.csv"),
        )

    middleware = DynamicContextMiddleware(context_provider=provider)
    update = middleware.before_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]
    assert update is not None
    state = {"messages": [], **update}

    first = _invoke(middleware, state)
    second = _invoke(middleware, state)

    assert calls == 1
    assert first == second
    assert first.startswith("static")
    assert "Current time: turn-1 (UTC)" in first
    assert "Workspace: ws-1" in first
    assert "Session: session-1" in first
    assert "Attachments: plan.md, data.csv" in first


def test_next_agent_run_refreshes_the_block() -> None:
    values = iter(("turn-1", "turn-2"))
    middleware = DynamicContextMiddleware(
        context_provider=lambda: DynamicContextBlock(next(values), "UTC"),
    )
    first = middleware.before_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]
    second = middleware.before_agent({"messages": [], **(first or {})}, runtime=None)  # type: ignore[arg-type]

    assert first != second
    assert "turn-1" in _invoke(middleware, {"messages": [], **(first or {})})
    assert "turn-2" in _invoke(middleware, {"messages": [], **(second or {})})


def test_no_provider_clears_checkpointed_block_and_injects_nothing() -> None:
    middleware = DynamicContextMiddleware()
    update = middleware.before_agent(
        {"messages": [], "dynamic_context_block": "stale"},
        runtime=None,  # type: ignore[arg-type]
    )
    assert update == {"dynamic_context_block": ""}
    assert _invoke(middleware, {"messages": [], **update}) == "static"


def test_render_collapses_absent_optional_fields() -> None:
    text = render_dynamic_block(DynamicContextBlock("2026-08-13 10:00:00", "Asia/Shanghai"))
    assert "Current time: 2026-08-13 10:00:00 (Asia/Shanghai)" in text
    assert "Workspace:" not in text
    assert "Session:" not in text
    assert "Attachments:" not in text


def test_sync_hook_rejects_async_provider() -> None:
    async def provider() -> DynamicContextBlock:
        return DynamicContextBlock("turn", "UTC")

    middleware = DynamicContextMiddleware(context_provider=provider)
    with pytest.raises(TypeError, match="async context_provider"):
        middleware.before_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]


def test_async_hook_resolves_provider_once_for_the_run() -> None:
    calls = 0

    async def provider() -> DynamicContextBlock:
        nonlocal calls
        calls += 1
        return DynamicContextBlock("async-turn", "UTC", workspace="async-ws")

    middleware = DynamicContextMiddleware(context_provider=provider)

    async def scenario() -> tuple[dict[str, str] | None, str]:
        update = await middleware.abefore_agent({"messages": []}, runtime=None)  # type: ignore[arg-type]

        async def handler(request: ModelRequest) -> str:
            return "\n".join([
                request.system_message.text,
                *(str(message.content) for message in request.messages),
            ])

        text = await middleware.awrap_model_call(
            _request({"messages": [], **(update or {})}),
            handler,  # type: ignore[arg-type]
        )
        return update, text

    update, text = asyncio.run(scenario())
    assert update is not None
    assert calls == 1
    assert "async-turn" in text
    assert "Workspace: async-ws" in text
