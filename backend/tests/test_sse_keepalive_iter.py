"""SSE 注释保活：SseDelivery 注入；raw 生产者侧不进总线心跳。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from config.env import LangfuseConfig
from domain.chat.delivery.sse import SSE_COMMENT_KEEPALIVE
from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    MemoryStreamBridge,
    iter_bridge_events,
)
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge
from services.qa_service import _yield_sse_from_agent_bridge


@pytest.mark.asyncio
async def test_keepalive_from_sse_delivery_timeout() -> None:
    async def slow_gen():
        await asyncio.sleep(0.12)
        yield {"type": "__tw_finish__", "finish_reason": "stop", "usage": {}}

    bridge = LangGraphSseBridge("sess-hb", assistant_message_id="msg-hb")
    builder = AssistantMessageBuilder(session_id="sess-hb", message_id="msg-hb")
    lines: list[str] = []
    async for line in _yield_sse_from_agent_bridge(
        slow_gen(),
        bridge=bridge,
        builder=builder,
        ctx={},
        session_id="sess-hb",
        user_id="u1",
        qa_type="COMMON_QA",
        keepalive_seconds=0.04,
    ):
        lines.append(line)

    assert any(line.startswith(": keepalive") or line == SSE_COMMENT_KEEPALIVE for line in lines)
    assert any("finish" in line for line in lines)


@pytest.mark.asyncio
async def test_keepalive_disabled_zero_interval() -> None:
    async def gen():
        yield {"type": "__tw_finish__", "finish_reason": "stop", "usage": {}}

    bridge = LangGraphSseBridge("sess-off", assistant_message_id="msg-off")
    builder = AssistantMessageBuilder(session_id="sess-off", message_id="msg-off")
    lines: list[str] = []
    async for line in _yield_sse_from_agent_bridge(
        gen(),
        bridge=bridge,
        builder=builder,
        ctx={},
        session_id="sess-off",
        user_id="u1",
        qa_type="COMMON_QA",
        keepalive_seconds=0,
    ):
        lines.append(line)

    assert not any(line.startswith(": keepalive") for line in lines)


@pytest.mark.asyncio
async def test_raw_bridge_zero_keepalive_no_heartbeat_sentinel() -> None:
    """生产者侧 keepalive=0 时不应产出 HEARTBEAT_SENTINEL（心跳仅在 SseDelivery）。"""

    async def slow_gen():
        await asyncio.sleep(0.08)
        yield {"type": "ok"}

    mem = MemoryStreamBridge()
    seen: list = []
    async for item in iter_bridge_events(
        mem,
        "run-raw",
        slow_gen(),
        keepalive_seconds=0,
    ):
        seen.append(item)

    assert not any(x is HEARTBEAT_SENTINEL for x in seen)
    assert any(isinstance(x, dict) and x.get("type") == "ok" for x in seen)
    assert any(x is END_SENTINEL for x in seen)


@pytest.mark.asyncio
async def test_sse_delivery_does_not_cancel_slow_producer_on_keepalive() -> None:
    cancelled = False

    async def slow_gen():
        nonlocal cancelled
        try:
            await asyncio.sleep(0.1)
            yield {"type": "text-delta", "text_delta": "ok"}
            yield {"type": "__tw_finish__", "finish_reason": "stop", "usage": {}}
        except asyncio.CancelledError:
            cancelled = True
            raise

    bridge = LangGraphSseBridge("sess-prod", assistant_message_id="msg-prod")
    builder = AssistantMessageBuilder(session_id="sess-prod", message_id="msg-prod")
    lines: list[str] = []
    async for line in _yield_sse_from_agent_bridge(
        slow_gen(),
        bridge=bridge,
        builder=builder,
        ctx={},
        session_id="sess-prod",
        user_id="u1",
        qa_type="COMMON_QA",
        keepalive_seconds=0.03,
    ):
        lines.append(line)

    assert not cancelled
    assert any("ok" in line or "text" in line for line in lines)


@pytest.mark.asyncio
async def test_test_case_stream_langfuse_context_with_sse_keepalive() -> None:
    async def gen():
        yield {"type": "phase-start"}
        await asyncio.sleep(0.06)
        yield {"type": "__tw_finish__", "finish_reason": "stop", "usage": {}}

    session_uuid = "d5f2c3f4-729c-4779-8dbe-307467f276e3"
    bridge = LangGraphSseBridge(session_uuid, assistant_message_id="asst-1")
    builder = AssistantMessageBuilder(session_id=session_uuid, message_id="asst-1")
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=None)
    mock_cm.__exit__ = MagicMock(return_value=False)
    langfuse_cfg = replace(LangfuseConfig, langfuse_tracing_enabled=True)
    lines: list[str] = []
    with (
        patch("config.env.LangfuseConfig", langfuse_cfg),
        patch("services.qa.helpers.LangfuseConfig", langfuse_cfg),
    ):
        with patch("langfuse.propagate_attributes", return_value=mock_cm):
            async for line in _yield_sse_from_agent_bridge(
                gen(),
                bridge=bridge,
                builder=builder,
                ctx={},
                session_id=session_uuid,
                user_id="u1",
                qa_type="TEST_CASE_QA",
                keepalive_seconds=0.02,
                langfuse_thread_id=f"case_graph_{session_uuid}",
            ):
                lines.append(line)

    assert any("finish" in line for line in lines)


@pytest.mark.asyncio
async def test_bridge_error_surfaces_through_orchestrator() -> None:
    async def bad_gen():
        yield {"type": "phase-start"}
        raise RuntimeError("upstream failed")

    bridge = LangGraphSseBridge("sess-err", assistant_message_id="msg-err")
    builder = AssistantMessageBuilder(session_id="sess-err", message_id="msg-err")
    langfuse_cfg = replace(LangfuseConfig, langfuse_tracing_enabled=False)
    with patch("services.qa.helpers.LangfuseConfig", langfuse_cfg):
        with pytest.raises(RuntimeError, match="upstream failed"):
            async for _ in _yield_sse_from_agent_bridge(
                bad_gen(),
                bridge=bridge,
                builder=builder,
                ctx={},
                session_id="sess-err",
                user_id="u1",
                qa_type="COMMON_QA",
                keepalive_seconds=0,
            ):
                pass


@pytest.mark.asyncio
async def test_test_case_phase_frames_through_orchestrator() -> None:
    async def case_gen():
        yield {"type": "phase-start", "phase": "scenes_testpoints"}
        yield {"type": "testpoints-confirm-required", "scenes": []}
        yield {"type": "__tw_finish__", "finish_reason": "stop", "usage": {}}

    bridge = LangGraphSseBridge("tc-phase", assistant_message_id="asst-phase")
    builder = AssistantMessageBuilder(session_id="tc-phase", message_id="asst-phase")
    lines: list[str] = []
    async for line in _yield_sse_from_agent_bridge(
        case_gen(),
        bridge=bridge,
        builder=builder,
        ctx={},
        session_id="tc-phase",
        user_id="u1",
        qa_type="TEST_CASE_QA",
        keepalive_seconds=0,
        langfuse_thread_id="case_graph_tc-phase",
    ):
        lines.append(line)

    joined = "".join(lines)
    assert "phase-start" in joined
    assert "testpoints-confirm-required" in joined
    assert "finish" in joined


@pytest.mark.asyncio
async def test_test_case_resume_scene_cases_through_orchestrator() -> None:
    async def resume_gen():
        yield {
            "type": "scene-cases",
            "sceneName": "上传",
            "cases": [{"case_id": "TC-001", "test_steps": ["s1"]}],
        }
        yield {"type": "__tw_finish__", "finish_reason": "stop", "total": 1, "usage": {}}

    bridge = LangGraphSseBridge("tc-resume", assistant_message_id="asst-resume")
    builder = AssistantMessageBuilder(session_id="tc-resume", message_id="asst-resume")
    langfuse_cfg = replace(LangfuseConfig, langfuse_tracing_enabled=False)
    lines: list[str] = []
    with patch("services.qa.helpers.LangfuseConfig", langfuse_cfg):
        async for line in _yield_sse_from_agent_bridge(
            resume_gen(),
            bridge=bridge,
            builder=builder,
            ctx={},
            session_id="tc-resume",
            user_id="u1",
            qa_type="TEST_CASE_QA",
            keepalive_seconds=0,
            langfuse_thread_id="case_graph_tc-resume",
        ):
            lines.append(line)

    joined = "".join(lines)
    assert "scene-cases" in joined
    assert "上传" in joined
