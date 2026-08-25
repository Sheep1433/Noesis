from __future__ import annotations

import asyncio
from typing import get_args, get_type_hints
from unittest.mock import AsyncMock

from langchain.agents.middleware.types import ModelRequest, PrivateStateAttr
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from noesis.agents.middlewares.memory_bulletin_middleware import (
    MemoryBulletinMiddleware,
    MemoryBulletinState,
)
from noesis.services.memory.bulletin import MemoryBulletin
from noesis.agents.memory_runtime import build_memory_bulletin_middleware


def test_bulletin_private_state_and_same_run_freeze() -> None:
    calls = 0

    async def provider(_query):
        nonlocal calls
        calls += 1
        return MemoryBulletin("bulletin", "a" * 64, ("memory-1",))

    middleware = MemoryBulletinMiddleware(run_id="run-1", provider=provider)
    first = asyncio.run(middleware.abefore_agent(
        {"messages": [HumanMessage(content="question")]}, runtime=None
    ))
    second = asyncio.run(middleware.abefore_agent(
        {"messages": [], **(first or {})}, runtime=None
    ))

    assert calls == 1
    assert second is None
    assert first["memory_bulletin_run_id"] == "run-1"
    for field in (
        "memory_bulletin_text",
        "memory_bulletin_hash",
        "memory_bulletin_ids",
        "memory_bulletin_source_snapshot",
    ):
        hint = get_type_hints(MemoryBulletinState, include_extras=True)[field]
        assert PrivateStateAttr in get_args(get_args(hint)[0])


def test_bulletin_is_late_inserted_without_mutating_stable_system_or_history() -> None:
    async def provider(_query):
        return MemoryBulletin("stable bulletin", "a" * 64, ("memory-1",))

    middleware = MemoryBulletinMiddleware(run_id="run-1", provider=provider)
    messages = [
        HumanMessage(content="old user"),
        AIMessage(content="old assistant"),
        HumanMessage(content="current user"),
    ]
    request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        system_message=SystemMessage(content="stable system"),
        messages=messages,
        state={"memory_bulletin_text": "stable bulletin"},
    )
    seen = []
    middleware.wrap_model_call(request, lambda value: seen.append(value))
    updated = seen[0]

    assert updated.system_message.text == "stable system"
    assert [message.content for message in updated.messages] == [
        "old user",
        "old assistant",
        "stable bulletin",
        "current user",
    ]
    assert updated.messages[2].additional_kwargs["noesis_late_context"] == "memory-bulletin"


def test_new_run_refreshes_but_equal_content_keeps_equal_hash_and_text() -> None:
    async def provider(_query):
        return MemoryBulletin("same bulletin", "b" * 64, ("memory-1",))

    first = asyncio.run(MemoryBulletinMiddleware(
        run_id="run-1", provider=provider
    ).abefore_agent({"messages": [HumanMessage(content="one")]}, runtime=None))
    second = asyncio.run(MemoryBulletinMiddleware(
        run_id="run-2", provider=provider
    ).abefore_agent({"messages": [HumanMessage(content="two")], **(first or {})}, runtime=None))

    assert first["memory_bulletin_text"] == second["memory_bulletin_text"]
    assert first["memory_bulletin_hash"] == second["memory_bulletin_hash"]
    assert second["memory_bulletin_run_id"] == "run-2"


def test_new_run_changed_content_changes_hash_without_rewriting_stable_prefix() -> None:
    async def first_provider(_query):
        return MemoryBulletin("old bulletin", "a" * 64, ("memory-1",))

    async def second_provider(_query):
        return MemoryBulletin("new bulletin", "b" * 64, ("memory-2",))

    first = asyncio.run(MemoryBulletinMiddleware(
        run_id="run-1", provider=first_provider
    ).abefore_agent({"messages": [HumanMessage(content="one")]}, runtime=None))
    second = asyncio.run(MemoryBulletinMiddleware(
        run_id="run-2", provider=second_provider
    ).abefore_agent({"messages": [HumanMessage(content="two")], **(first or {})}, runtime=None))

    assert first["memory_bulletin_text"] != second["memory_bulletin_text"]
    assert first["memory_bulletin_hash"] != second["memory_bulletin_hash"]
    assert second["memory_bulletin_ids"] == ["memory-2"]


def test_private_recall_persistence_failure_forces_zero_injection(monkeypatch) -> None:
    monkeypatch.setattr(
        "noesis.agents.memory_runtime.resolve_scope_key", lambda *_args, **_kw: "profile:SUPER_AGENT_QA|project:project"
    )
    monkeypatch.setattr(
        "noesis.agents.memory_runtime.MemoryBulletinService.build",
        AsyncMock(return_value=MemoryBulletin("do old thing", "a" * 64, ("memory-1",))),
    )
    monkeypatch.setattr(
        "noesis.agents.memory_runtime.MemoryCaptureService.record_recalled_bulletin",
        AsyncMock(side_effect=RuntimeError("postgres unavailable")),
    )
    middleware = build_memory_bulletin_middleware(
        db=object(),  # type: ignore[arg-type]
        user_id="user-1",
        session_id="session-1",
        run_id="run-1",
        agent_profile="SUPER_AGENT_QA",
    )

    state = asyncio.run(
        middleware.abefore_agent(
            {"messages": [HumanMessage(content="question")]}, runtime=None
        )
    )

    assert state["memory_bulletin_text"] == ""
    assert state["memory_bulletin_ids"] == []
