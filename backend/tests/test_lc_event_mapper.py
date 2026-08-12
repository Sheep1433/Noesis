"""RuntimeEventMapper + SseDelivery 契约冒烟。"""
from __future__ import annotations

from noesis.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    RunPaused,
    StreamDone,
    WireFrame,
)
from noesis.chat.delivery.sse import encode_run_event
from noesis.chat.delivery.sse import encode_filtered
from noesis.chat.event_mapping.mapper import RuntimeEventMapper
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge


def test_runtime_event_mapper_is_the_single_mapping_entry_point() -> None:
    """RuntimeEventMapper 是 raw event → typed RunEvent 的唯一映射入口。

    Web、Channel、cron、eval 共用此 mapper。
    """
    bridge = LangGraphSseBridge("s1", assistant_message_id="m1")
    mapper = RuntimeEventMapper(bridge)
    builder = AssistantMessageBuilder(session_id="s1", message_id="m1")
    ctx: dict = {}
    events = mapper.map_item(
        {"type": "text-delta", "text_delta": "via-mapper"},
        builder,
        ctx,
    )
    assert events
    # raw event 经一次 mapping 产出 typed RunEvent（WireFrame）
    assert any(isinstance(e, WireFrame) for e in events)


def test_map_hitl_required_and_paused() -> None:
    bridge = LangGraphSseBridge("s1", assistant_message_id="m1")
    mapper = RuntimeEventMapper(bridge)
    evs = mapper.map_item(
        {"type": "hitl-required", "interrupt_id": "x", "kind": "approval"},
        None,
        {},
    )
    assert any(isinstance(event, HitlRequired) for event in evs)

    evs2 = mapper.map_item(
        {"type": "__tw_finish__", "finish_reason": "hitl_pending", "usage": {}},
        None,
        {},
    )
    assert any(isinstance(e, RunPaused) for e in evs2)


def test_encode_roundtrip_text_delta() -> None:
    bridge = LangGraphSseBridge("s1", assistant_message_id="m1")
    mapper = RuntimeEventMapper(bridge)
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
    mapper = RuntimeEventMapper(bridge)
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


def test_finish_reason_maps_to_correct_terminal_semantics() -> None:
    cases = {
        "stop": RunCompleted,
        "length_stop": RunAborted,
        "tool_loop_limit": RunAborted,
        "context_exhausted": RunError,
        "retryable_error": RunError,
    }
    for reason, expected_type in cases.items():
        mapper = RuntimeEventMapper(
            LangGraphSseBridge("s1", assistant_message_id=f"m-{reason}")
        )
        events = mapper.map_item(
            {"type": "__tw_finish__", "finish_reason": reason, "usage": {}},
            None,
            {},
        )
        assert any(isinstance(event, expected_type) for event in events), reason


def test_unknown_business_event_is_logged_and_dropped() -> None:
    mapper = RuntimeEventMapper(LangGraphSseBridge("s1", assistant_message_id="m1"))
    assert mapper.map_item({"type": "future-unknown-event", "value": 1}, None, {}) == []


def test_wire_frame_keeps_model_attempt_identity() -> None:
    mapper = RuntimeEventMapper(LangGraphSseBridge("s1", assistant_message_id="m1"))
    mapper.map_item(
        {"event": "on_chat_model_start", "run_id": "model-old", "data": {}},
        None,
        {},
    )
    retry = mapper.map_item(
        {
            "event": "on_custom_event",
            "name": "noesis_model_retry",
            "data": {"attempt_id": 2, "status": "retrying"},
        },
        None,
        {},
    )
    assert isinstance(retry[0], WireFrame)
    assert retry[0].attempt_id == 1
    mapper.map_item(
        {"event": "on_chat_model_start", "run_id": "model-new", "data": {}},
        None,
        {},
    )
    late = mapper.map_item(
        {"type": "text-delta", "run_id": "model-old", "text_delta": "late"},
        None,
        {},
    )
    current = mapper.map_item(
        {"type": "text-delta", "run_id": "model-new", "text_delta": "current"},
        None,
        {},
    )
    assert isinstance(late[-1], WireFrame) and late[-1].attempt_id == 1
    assert isinstance(current[-1], WireFrame) and current[-1].attempt_id == 2
