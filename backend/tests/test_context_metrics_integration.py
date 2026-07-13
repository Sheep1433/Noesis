"""Context metrics 与 Agent 流式路径集成测试。"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agent.factory import create_noesis_agent
from agent.middlewares.context_metrics_middleware import ContextMetricsRegistry
from config.checkpointer import close_checkpointer, get_checkpointer, init_checkpointer


class _FakeLLM(GenericFakeChatModel):
    def get_num_tokens(self, text: str) -> int:  # noqa: ARG002
        return 100


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires the Compose PostgreSQL service",
)
async def test_agent_stream_writes_context_registry_by_thread_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.env import get_config

    get_config.get_checkpoint_config.cache_clear()
    await close_checkpointer()
    await init_checkpointer()

    cfg = SimpleNamespace(
        context_display_enabled=True,
        context_max_input_tokens=128000,
        dangling_tool_call_repair_enabled=False,
        loop_detection_enabled=False,
        summarization_enabled=False,
        tool_call_limit_enabled=False,
        show_thinking_process="false",
    )
    session_id = "sess-context-integration"
    fake_llm = _FakeLLM(messages=iter([AIMessage(content="ok")]))

    try:
        with (
            patch("agent.factory.ModelConfig", cfg),
            patch("agent.factory.get_llm", return_value=fake_llm),
            patch("agent.middlewares.context_metrics_middleware.ModelConfig", cfg),
            patch("agent.middlewares.context_metrics.resolve_context_max_tokens", return_value=128000),
        ):
            agent = create_noesis_agent(
                tools=[],
                system_prompt="You are a test assistant.",
                checkpointer=get_checkpointer(),
            )
            config = {"configurable": {"thread_id": session_id}}
            async for _ in agent.astream(
                {"messages": [HumanMessage(content="hi")]},
                config=config,
                stream_mode="updates",
            ):
                pass

        snap = ContextMetricsRegistry.peek(session_id)
        assert snap is not None
        assert snap["max_tokens"] == 128000
        assert snap["current_tokens"] > 0
        assert snap["used_percentage"] >= 1
    finally:
        ContextMetricsRegistry.clear(session_id)
        await close_checkpointer()
        get_config.get_checkpoint_config.cache_clear()
