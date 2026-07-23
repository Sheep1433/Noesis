"""RunOrchestrator.run_headless 无 SSE、仍触发 on_events。"""
from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List

import pytest

from domain.chat.delivery.events import RunCompleted, WireFrame
from domain.chat.delivery.orchestrator import RunOrchestrator
from domain.chat.delivery.persist_sink import PersistSink
from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge


async def _fake_agent() -> AsyncGenerator[Any, None]:
    yield {
        "event": "on_chat_model_stream",
        "data": {"chunk": type("C", (), {"content": "hi"})()},
        "run_id": "r1",
        "name": "ChatModel",
        "tags": [],
        "metadata": {},
    }
    # 简化：直接给 bridge 可识别的控制 dict（若 stream 路径复杂则 mock mapper）
    return


@pytest.mark.asyncio
async def test_run_headless_publishes_and_finalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = RunOrchestrator()
    bridge = LangGraphSseBridge("sess-h")
    builder = AssistantMessageBuilder(session_id="sess-h", message_id=bridge.assistant_message_id)
    ctx: Dict[str, Any] = {}
    sink = PersistSink()
    seen: List[str] = []

    async def on_events(events):
        for ev in events:
            sink.on_event(ev)
            seen.append(type(ev).__name__)

    async def fake_produce(*args, **kwargs):
        # 直接模拟 agent 产出经 on_events
        evs = [
            WireFrame(event="text-delta", data={"type": "text-delta", "delta": "x"}),
            RunCompleted(finish_reason="stop"),
        ]
        await on_events(evs)

    monkeypatch.setattr(orch, "_produce", fake_produce)

    async def empty_gen():
        if False:
            yield None

    await orch.run_headless(
        empty_gen(),
        bridge=bridge,
        builder=builder,
        ctx=ctx,
        session_id="sess-h",
        origin="telegram",
        on_events=on_events,
    )
    assert "RunCompleted" in seen
    assert sink.final_decision().kind == "completed"
