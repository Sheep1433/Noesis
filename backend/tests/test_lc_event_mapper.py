"""LcEventMapper + SseCodec 契约冒烟。"""
from __future__ import annotations

from domain.chat.delivery.events import HitlRequired, RunPaused, StreamDone, WireFrame
from domain.chat.delivery.sse import LcEventMapper
from domain.chat.delivery.sse import encode_run_event, parse_sse_line_to_event
from domain.chat.delivery.sse import encode_filtered
from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge


def test_parse_hitl_required_and_paused() -> None:
    line = (
        'event: hitl-required\n'
        'data: {"type": "hitl-required", "interrupt_id": "x", "kind": "approval"}\n\n'
    )
    evs = parse_sse_line_to_event(line)
    assert len(evs) == 1
    assert isinstance(evs[0], HitlRequired)

    finish = (
        'event: finish\n'
        'data: {"type": "finish", "finish_reason": "hitl_pending", "usage": {}}\n\n'
    )
    evs2 = parse_sse_line_to_event(finish)
    assert any(isinstance(e, RunPaused) for e in evs2)
    assert any(isinstance(e, WireFrame) for e in evs2)


def test_encode_roundtrip_text_delta() -> None:
    bridge = LangGraphSseBridge("s1", assistant_message_id="m1")
    mapper = LcEventMapper(bridge)
    builder = AssistantMessageBuilder(session_id="s1", message_id="m1")
    ctx: dict = {}
    events = mapper.map_item(
        {"type": "text-delta", "text_delta": "hello"},
        builder,
        ctx,
    )
    assert events
    lines: list[str] = []
    for ev in events:
        lines.extend(encode_filtered(ev))
    joined = "".join(lines)
    assert "text-delta" in joined or "text-start" in joined
    assert "hello" in joined


def test_finalize_emits_done() -> None:
    bridge = LangGraphSseBridge("s1", assistant_message_id="m1")
    mapper = LcEventMapper(bridge)
    events = mapper.finalize(finish_reason="stop")
    assert any(isinstance(e, StreamDone) for e in events)
    lines = []
    for ev in events:
        lines.extend(encode_run_event(ev) if not isinstance(ev, StreamDone) else encode_filtered(ev))
    # StreamDone via encode_filtered
    lines2 = []
    for ev in events:
        lines2.extend(encode_filtered(ev))
    assert any("[DONE]" in x for x in lines2)
