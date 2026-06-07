"""SSE 注释保活：``_iter_agent_items_with_keepalive`` 竞速逻辑。"""
from __future__ import annotations

import asyncio

import pytest

from services.qa_service import KEEPALIVE_TICK, _iter_agent_items_with_keepalive


@pytest.mark.asyncio
async def test_keepalive_tick_when_upstream_slow() -> None:
    async def slow_gen():
        await asyncio.sleep(0.12)
        yield {"type": "finish", "finishReason": "stop", "usage": {}}

    seen: list = []
    async for item in _iter_agent_items_with_keepalive(slow_gen(), 0.04):
        seen.append(item)

    assert any(x is KEEPALIVE_TICK for x in seen), "应在首包前至少插入一次保活占位"
    assert any(isinstance(x, dict) and x.get("type") == "finish" for x in seen)


@pytest.mark.asyncio
async def test_keepalive_disabled_zero_interval() -> None:
    async def gen():
        yield {"type": "finish", "finishReason": "stop", "usage": {}}

    seen: list = []
    async for item in _iter_agent_items_with_keepalive(gen(), 0):
        seen.append(item)

    assert not any(x is KEEPALIVE_TICK for x in seen)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_test_case_coordinator_stream_langfuse_context_with_keepalive() -> None:
    """Langfuse workflow context 在 qa_service 消费层包裹，保活多帧不应触发 ContextVar reset 异常。"""
    from dataclasses import replace
    from unittest.mock import MagicMock, patch

    from config.env import LangfuseConfig
    from services.qa_service import _iter_test_case_coordinator_stream

    async def gen():
        yield {"type": "phase-start"}
        await asyncio.sleep(0.06)
        yield {"type": "finish", "finishReason": "stop", "usage": {}}

    seen: list = []
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=None)
    mock_cm.__exit__ = MagicMock(return_value=False)
    session_uuid = "d5f2c3f4-729c-4779-8dbe-307467f276e3"
    langfuse_cfg = replace(LangfuseConfig, langfuse_tracing_enabled=True)
    with (
        patch("config.env.LangfuseConfig", langfuse_cfg),
        patch("services.qa_service.LangfuseConfig", langfuse_cfg),
    ):
        with patch("langfuse.propagate_attributes", return_value=mock_cm):
            async for item in _iter_test_case_coordinator_stream(
                gen(),
                session_uuid,
                "TEST_CASE_QA",
                0.02,
            ):
                seen.append(item)

    assert any(isinstance(x, dict) and x.get("type") == "finish" for x in seen)
