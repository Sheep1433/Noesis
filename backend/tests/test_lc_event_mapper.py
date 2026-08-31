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
    # length_stop / safety_stop 走 completed 分支：输出与 usage 完整（只是最后
    # 一步被 provider 截断/安全收尾），客户端契约里这两个值随 finish 帧结算；
    # 转 RunAborted 会发 abort 帧，客户端没有服务端主动 abort 的处理器。
    cases = {
        "stop": RunCompleted,
        "length_stop": RunCompleted,
        "safety_stop": RunCompleted,
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


def test_model_retry_event_is_emitted_as_run_status_type() -> None:
    """noesis_model_retry custom event 必须以 run-status 类型透传给前端。

    中间件 ``_build_retry_event`` 产出的 payload 含 ``type: noesis_model_retry``。
    bridge 转成 run-status WireFrame 时不得让该 type 覆盖外层 ``run-status``，
    否则前端按 ``data.type`` 分发时匹配不到 ``run-status`` 分支，重试提示静默丢失。
    """
    mapper = RuntimeEventMapper(LangGraphSseBridge("s1", assistant_message_id="m1"))
    # 与 LLMErrorHandlingMiddleware._build_retry_event 返回结构一致
    retry_data = {
        "type": "noesis_model_retry",
        "status": "retrying",
        "attempt_id": 1,
        "max_attempts": 6,
        "wait_ms": 1091,
        "reason": "transient",
        "message": "连接失败，provider 请求暂时失败，正在重试 (1/6)，2s 后重试",
    }
    events = mapper.map_item(
        {"event": "on_custom_event", "name": "noesis_model_retry", "data": retry_data},
        None,
        {},
    )
    assert len(events) == 1
    wf = events[0]
    assert isinstance(wf, WireFrame)
    assert wf.event == "run-status"
    # 前端 useSSEStream 按 data.type 分发，必须是 run-status
    assert wf.data["type"] == "run-status"
    # retry 语义字段保留
    assert wf.data["status"] == "retrying"
    assert wf.data["max_attempts"] == 6


def test_model_fallback_event_emits_text_and_error() -> None:
    """noesis_model_fallback custom event 必须把 fallback 文本推入 builder
    并发 error 事件，让 projection 标 ERROR、前端显示失败消息。

    根因：中间件 awrap_model_call 返回 fallback AIMessage 绕过 model.ainvoke，
    不触发 on_chat_model_end，旧链路（on_chat_model_end 检测 noesis_error_fallback）
    是死代码。fallback 改由 custom event 通道传输。
    """
    bridge = LangGraphSseBridge("s1", assistant_message_id="m1")
    mapper = RuntimeEventMapper(bridge)
    builder = AssistantMessageBuilder(session_id="s1", message_id="m1")
    fallback_data = {
        "type": "noesis_model_fallback",
        "content": "LLM 服务经多次重试后仍不可用，请稍候继续对话。",
        "error_type": "APITimeoutError",
        "error_reason": "transient",
        "error_detail": "Request timed out.",
    }
    events = mapper.map_item(
        {"event": "on_custom_event", "name": "noesis_model_fallback", "data": fallback_data},
        builder,
        {},
    )
    # fallback 文本写进 builder
    parts = builder.to_dict()["parts"]
    assert any(
        isinstance(p, dict) and p.get("type") == "text" and "LLM 服务经多次重试后仍不可用" in p.get("content", "")
        for p in parts
    )
    # 产出包含 text-delta（前端显示文本）和 error（RunError → projection 标 ERROR）
    from noesis.chat.delivery.events import RunError
    event_types = [getattr(e, "event", None) for e in events]
    assert "text-delta" in event_types
    assert any(isinstance(e, RunError) for e in events)
    # bridge 已发终态事件，后续 finish 不再重复
    assert bridge._finish_emitted is True


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
