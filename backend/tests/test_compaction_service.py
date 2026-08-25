from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from noesis.agents.middlewares.compaction_middleware import (
    CompactionMiddleware,
    CompactionThresholds,
)


@pytest.mark.asyncio
async def test_compact_session_updates_checkpoint_without_creating_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import noesis.services.compaction_service as service
    from langchain.agents import create_agent

    saver = InMemorySaver()
    seed = create_agent(
        model=FakeListChatModel(responses=["ok"]),
        tools=[],
        system_prompt="seed",
        checkpointer=saver,
    )
    config = {"configurable": {"thread_id": "session-1"}}
    async for _ in seed.astream(
        {"messages": [HumanMessage(content="old context")]},
        config=config,
        stream_mode="values",
    ):
        pass

    middleware = CompactionMiddleware(
        token_counter=lambda messages: len(messages) * 10,
        summarize=lambda messages: "host summary",
        thresholds=CompactionThresholds(1000, 10, 100),
        keep_messages=1,
    )
    factory_args = []

    @asynccontextmanager
    async def fake_db():
        yield object()

    class FakeRuns:
        def __init__(self, db):
            pass

        async def get_active_for_session(self, user_id, session_id):
            return None

    monkeypatch.setattr(service.pg_manager, "get_async_session_context", fake_db)

    async def fake_session(*args, **kwargs):
        return _session()

    monkeypatch.setattr(
        service.ChatService,
        "get_session_by_id",
        staticmethod(fake_session),
    )
    monkeypatch.setattr(service, "AgentRunRepository", FakeRuns)
    monkeypatch.setattr(service, "_resolve_model_id", _fake_model_id)
    monkeypatch.setattr(service, "get_checkpointer", lambda: saver)
    def fake_build(**kwargs):
        factory_args.append(kwargs)
        return middleware

    monkeypatch.setattr(service, "build_compaction_middleware", fake_build)
    monkeypatch.setattr(service, "get_llm", lambda **kwargs: FakeListChatModel(responses=["unused"]))

    outcome = await service.compact_session(session_id="session-1", user_id="user-1")

    assert outcome.status == "completed"
    assert factory_args == [{"model_id": "test-model", "backend": None}]
    assert outcome.pre_message_count == 2
    assert outcome.post_message_count == 2
    observer = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        tools=[],
        system_prompt="observer",
        middleware=[middleware],
        checkpointer=saver,
    )
    state = await observer.aget_state(config)
    assert len(state.values["messages"]) == 2
    assert state.values["compaction"]["last_mode"] == "manual"
    assert state.values["compaction"]["event"]["cutoff_index"] == 1


def _session():
    return SimpleNamespace(extra={"qa_type": "COMMON_QA"})


async def _fake_model_id(session_id: str, user_id: str, db: object) -> str:
    return "test-model"
