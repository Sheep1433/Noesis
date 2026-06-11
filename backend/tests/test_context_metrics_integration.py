"""Context metrics 与真实 Agent 流式路径集成测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from agent.factory import create_noesis_agent
from agent.middlewares.context_metrics_middleware import ContextMetricsRegistry
from config.checkpointer import get_checkpointer, init_checkpointer


@pytest.mark.asyncio
async def test_agent_stream_writes_context_registry_by_thread_id() -> None:
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
    with (
        patch("agent.factory.ModelConfig", cfg),
        patch("agent.middlewares.context_metrics_middleware.ModelConfig", cfg),
        patch("agent.middlewares.context_metrics.ModelConfig", cfg),
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
    ContextMetricsRegistry.clear(session_id)
