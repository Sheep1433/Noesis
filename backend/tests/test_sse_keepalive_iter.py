"""SSE 注释保活：StreamBridge 订阅超时与 Langfuse ContextVar 回归。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from config.env import LangfuseConfig
from services.qa_service import SSE_COMMENT_KEEPALIVE, _iter_agent_stream_via_bridge
from utils.stream_bridge import HEARTBEAT_SENTINEL, StreamBridgeError


@pytest.mark.asyncio
async def test_keepalive_from_bridge_subscribe_timeout() -> None:
    async def slow_gen():
        await asyncio.sleep(0.12)
        yield {"type": "finish", "finishReason": "stop", "usage": {}}

    seen: list = []
    async for item in _iter_agent_stream_via_bridge(
        slow_gen(),
        session_id="sess-hb",
        assistant_message_id="msg-hb",
        qa_type="COMMON_QA",
        keepalive_seconds=0.04,
    ):
        seen.append(item)

    assert any(x is HEARTBEAT_SENTINEL for x in seen), "订阅空闲超时应产出心跳哨兵"
    assert any(isinstance(x, dict) and x.get("type") == "finish" for x in seen)


@pytest.mark.asyncio
async def test_keepalive_disabled_zero_interval() -> None:
    async def gen():
        yield {"type": "finish", "finishReason": "stop", "usage": {}}

    seen: list = []
    async for item in _iter_agent_stream_via_bridge(
        gen(),
        session_id="sess-off",
        assistant_message_id="msg-off",
        qa_type="COMMON_QA",
        keepalive_seconds=0,
    ):
        seen.append(item)

    assert not any(x is HEARTBEAT_SENTINEL for x in seen)
    assert len([x for x in seen if isinstance(x, dict)]) == 1


@pytest.mark.asyncio
async def test_bridge_does_not_cancel_slow_producer_on_heartbeat() -> None:
    """心跳期间上游 generator 应继续运行，不被 wait_for 取消。"""
    cancelled = False

    async def slow_gen():
        nonlocal cancelled
        try:
            await asyncio.sleep(0.1)
            yield {"type": "ok"}
        except asyncio.CancelledError:
            cancelled = True
            raise

    seen: list = []
    async for item in _iter_agent_stream_via_bridge(
        slow_gen(),
        session_id="sess-prod",
        assistant_message_id="msg-prod",
        qa_type="COMMON_QA",
        keepalive_seconds=0.03,
    ):
        seen.append(item)

    assert not cancelled
    assert any(isinstance(x, dict) and x.get("type") == "ok" for x in seen)


@pytest.mark.asyncio
async def test_test_case_stream_langfuse_context_with_bridge_keepalive() -> None:
    """Langfuse workflow context 在生产者 Task 内，保活多帧不应触发 ContextVar reset 异常。"""
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
            async for item in _iter_agent_stream_via_bridge(
                gen(),
                session_id=session_uuid,
                assistant_message_id="asst-1",
                qa_type="TEST_CASE_QA",
                keepalive_seconds=0.02,
                langfuse_thread_id=f"case_graph_{session_uuid}",
            ):
                seen.append(item)

    assert any(isinstance(x, dict) and x.get("type") == "finish" for x in seen)


@pytest.mark.asyncio
async def test_bridge_error_surfaces_as_stream_bridge_error() -> None:
    async def bad_gen():
        yield {"type": "phase-start"}
        raise RuntimeError("upstream failed")

    seen: list = []
    langfuse_cfg = replace(LangfuseConfig, langfuse_tracing_enabled=False)
    with patch("services.qa_service.LangfuseConfig", langfuse_cfg):
        async for item in _iter_agent_stream_via_bridge(
            bad_gen(),
            session_id="sess-err",
            assistant_message_id="msg-err",
            qa_type="COMMON_QA",
            keepalive_seconds=0,
        ):
            seen.append(item)

    assert any(isinstance(x, dict) and x.get("type") == "phase-start" for x in seen)
    errors = [x for x in seen if isinstance(x, StreamBridgeError)]
    assert errors and "upstream failed" in str(errors[0].exc)


@pytest.mark.asyncio
async def test_test_case_phase_frames_through_bridge() -> None:
    """TEST_CASE_QA 阶段业务帧经 bridge 转发，不被保活逻辑改写。"""
    async def case_gen():
        yield {"type": "phase-start", "phase": "scenes_testpoints"}
        yield {"type": "testpoints-confirm-required", "scenes": []}
        yield {"type": "finish", "finishReason": "stop", "usage": {}}

    seen: list = []
    async for item in _iter_agent_stream_via_bridge(
        case_gen(),
        session_id="tc-phase",
        assistant_message_id="asst-phase",
        qa_type="TEST_CASE_QA",
        keepalive_seconds=0,
        langfuse_thread_id="case_graph_tc-phase",
    ):
        if isinstance(item, StreamBridgeError):
            raise item.exc
        seen.append(item)

    types = [x.get("type") for x in seen if isinstance(x, dict)]
    assert types == ["phase-start", "testpoints-confirm-required", "finish"]


@pytest.mark.asyncio
async def test_test_case_resume_scene_cases_through_bridge() -> None:
    async def resume_gen():
        yield {
            "type": "scene-cases",
            "sceneName": "上传",
            "cases": [{"case_id": "TC-001", "test_steps": ["s1"]}],
        }
        yield {"type": "finish", "finishReason": "stop", "total": 1, "usage": {}}

    seen: list = []
    langfuse_cfg = replace(LangfuseConfig, langfuse_tracing_enabled=False)
    with patch("services.qa_service.LangfuseConfig", langfuse_cfg):
        async for item in _iter_agent_stream_via_bridge(
            resume_gen(),
            session_id="tc-resume",
            assistant_message_id="asst-resume",
            qa_type="TEST_CASE_QA",
            keepalive_seconds=0,
            langfuse_thread_id="case_graph_tc-resume",
        ):
            seen.append(item)

    scene = next(x for x in seen if isinstance(x, dict) and x.get("type") == "scene-cases")
    assert scene.get("sceneName") == "上传"
    finish = next(x for x in seen if isinstance(x, dict) and x.get("type") == "finish")
    assert finish.get("total") == 1
