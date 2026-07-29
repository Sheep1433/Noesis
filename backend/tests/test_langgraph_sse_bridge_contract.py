"""LangGraphSseBridge → Noesis SSE 字符串的最小契约断言（防静默破坏 useSSEStream）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from noesis_server.domain.chat.streaming.langgraph_sse import TASK_TOOL_NAME, LangGraphSseBridge, bridge_raw_to_sse_lines
from noesis.errors.tool_failure import ToolInfrastructureError
from noesis_server.domain.chat.streaming.bridge import END_SENTINEL, HEARTBEAT_SENTINEL
from noesis_server.domain.chat.message_builder import AssistantMessageBuilder, ToolPart


def _ctx() -> Dict[str, Any]:
    return {
        "text_buffer": "",
        "current_tool_name": None,
        "current_tool_call_id": None,
        "tool_start_times": {},
        "usage_cumulative": {"input_tokens": 0, "output_tokens": 0},
        "usage_seen_run_ids": set(),
    }


def _data_json_objects(sse_text: str) -> List[Dict[str, Any]]:
    """从 SSE 文本中提取每条 ``data:`` JSON（跳过 ``[DONE]``）。"""
    out: List[Dict[str, Any]] = []
    for frame in sse_text.split("\n\n"):
        if not frame.strip():
            continue
        for line in frame.split("\n"):
            if not line.startswith("data: "):
                continue
            payload = line.removeprefix("data: ").strip()
            if payload == "[DONE]":
                continue
            out.append(json.loads(payload))
    return out


def test_message_start_and_text_delta_shapes() -> None:
    bridge = LangGraphSseBridge("sess-1")
    builder = AssistantMessageBuilder(session_id="sess-1", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    chunks: List[str] = []
    chunks.extend(bridge.process_item({"type": "text-delta", "text_delta": "hi"}, builder, ctx))
    text = "".join(chunks)
    assert text.endswith("\n\n")
    assert "event: message-start\n" in text
    assert "event: text-start\n" in text
    assert "event: text-delta\n" in text
    objs = _data_json_objects(text)
    assert objs[0]["type"] == "message-start"
    assert objs[0]["session_id"] == "sess-1"
    assert objs[0]["assistant_message_id"] == bridge.assistant_message_id
    assert "langfuse_session_id" not in objs[0]
    td = [o for o in objs if o["type"] == "text-delta"][0]
    assert td["text_delta"] == "hi"
    assert "part_id" in td


def test_kb_retrieval_and_terminal_annotation_event_order() -> None:
    bridge = LangGraphSseBridge("sess-citation")
    builder = AssistantMessageBuilder(
        session_id="sess-citation",
        message_id=bridge.assistant_message_id,
    )
    ctx = _ctx()
    start = {
        "event": "on_tool_start",
        "name": "search_knowledge_base",
        "run_id": "call-1",
        "data": {"input": {"query": "验证码"}},
    }
    bridge.process_item(start, builder, ctx)
    result = {
        "results": [{
            "collection_name": "requirements",
            "document_id": "doc_1",
            "document_version_id": "docv_1",
            "segment_id": "seg_1",
            "file_name": "登录.md",
            "excerpt": "验证码五分钟有效",
            "citable": True,
            "evidence_id": "ev_1",
            "tool_call_ids": ["call-1"],
        }],
    }
    end = {
        "event": "on_tool_end",
        "name": "search_knowledge_base",
        "run_id": "call-1",
        "data": {"output": json.dumps(result, ensure_ascii=False)},
    }
    retrieval_lines = bridge.process_item(end, builder, ctx)
    retrieval_events = _data_json_objects("".join(retrieval_lines))
    retrieval = next(item for item in retrieval_events if item["type"] == "retrieval-results-available")
    assert retrieval["tool_call_id"] == "call-1"
    assert retrieval["results"][0]["evidence_id"] == "ev_1"

    answer_lines = bridge.process_item({
        "type": "typed-answer-segments",
        "segments": [{"text": "五分钟", "cited_evidence_ids": ["ev_1"]}],
    }, builder, ctx)
    answer_blob = "".join(answer_lines)
    assert answer_blob.index("event: text-end") < answer_blob.index("event: text-annotation-added")
    annotation = next(
        item for item in _data_json_objects(answer_blob)
        if item["type"] == "text-annotation-added"
    )
    assert annotation["text_part_id"]
    assert annotation["annotation"]["evidence_id"] == "ev_1"


def test_structured_answer_transport_tool_is_not_user_visible() -> None:
    bridge = LangGraphSseBridge("sess-structured")
    builder = AssistantMessageBuilder()
    ctx = _ctx()
    start = bridge.process_item({
        "event": "on_tool_start",
        "name": "CitedAnswer",
        "run_id": "structured-1",
        "data": {"input": {"segments": []}},
    }, builder, ctx)
    end = bridge.process_item({
        "event": "on_tool_end",
        "name": "CitedAnswer",
        "run_id": "structured-1",
        "data": {"output": "ok"},
    }, builder, ctx)
    assert start == []
    assert end == []
    assert builder.to_dict() == {"parts": []}


def test_completed_segment_annotation_precedes_text_end() -> None:
    bridge = LangGraphSseBridge("sess-segments")
    builder = AssistantMessageBuilder(message_id=bridge.assistant_message_id)
    builder.register_retrieval_results(
        tool_call_id="call-1",
        query="有效期",
        results=[{
            "collection_name": "requirements", "document_id": "doc_1",
            "document_version_id": "docv_1", "segment_id": "seg_1",
            "file_name": "需求.md", "excerpt": "五分钟", "citable": True,
            "evidence_id": "ev_1", "tool_call_ids": ["call-1"],
        }],
    )
    ctx = _ctx()
    segment = bridge.process_item({
        "type": "typed-answer-segment",
        "segment": {"text": "五分钟", "cited_evidence_ids": ["ev_1"]},
    }, builder, ctx)
    finish = bridge.process_item({"type": "__tw_finish__", "finish_reason": "stop"}, builder, ctx)
    blob = "".join([*segment, *finish])
    assert blob.index("event: text-annotation-added") < blob.index("event: text-end")


def test_message_start_does_not_include_client_stop_token() -> None:
    bridge = LangGraphSseBridge("sess-stop")
    builder = AssistantMessageBuilder(session_id="sess-stop", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    text = "".join(bridge.process_item({"type": "text-delta", "text_delta": "x"}, builder, ctx))
    objs = _data_json_objects(text)
    assert objs[0]["type"] == "message-start"
    assert "stop_token" not in objs[0]


def test_message_start_with_langfuse_hint() -> None:
    bridge = LangGraphSseBridge("sess-lf", emit_langfuse_session_hint=True)
    builder = AssistantMessageBuilder(session_id="sess-lf", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    text = "".join(bridge.process_item({"type": "text-delta", "text_delta": "x"}, builder, ctx))
    objs = _data_json_objects(text)
    assert objs[0]["type"] == "message-start"
    assert objs[0]["langfuse_session_id"] == "sess-lf"


def test_context_update_emitted_with_usage_update() -> None:
    from noesis.middlewares.context_metrics_middleware import ContextMetricsRegistry

    bridge = LangGraphSseBridge("sess-usage-ctx")
    builder = AssistantMessageBuilder(session_id="sess-usage-ctx", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    ContextMetricsRegistry.put(
        "sess-usage-ctx",
        {"current_tokens": 1200, "max_tokens": 128000, "used_percentage": 1},
    )
    parts: List[str] = []
    parts.extend(bridge.process_item({"type": "text-delta", "text_delta": "hi"}, builder, ctx))
    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_end",
                "data": {"output": MagicMock(usage_metadata={"input_tokens": 10, "output_tokens": 5})},
            },
            builder,
            ctx,
        )
    )
    blob = "".join(parts)
    assert "event: usage-update\n" in blob
    assert "event: context-update\n" in blob
    ContextMetricsRegistry.clear("sess-usage-ctx")


def test_context_update_event_shape() -> None:
    from noesis.middlewares.context_metrics_middleware import ContextMetricsRegistry

    bridge = LangGraphSseBridge("sess-ctx")
    builder = AssistantMessageBuilder(session_id="sess-ctx", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    ContextMetricsRegistry.put(
        "sess-ctx",
        {"current_tokens": 87040, "max_tokens": 128000, "used_percentage": 68},
    )
    blob = "".join(
        bridge.process_item(
            {
                "event": "on_chat_model_end",
                "data": {"output": MagicMock(usage_metadata={"input_tokens": 1, "output_tokens": 1})},
            },
            builder,
            ctx,
        )
    )
    assert "event: context-update\n" in blob
    cu = [o for o in _data_json_objects(blob) if o.get("type") == "context-update"][0]
    assert cu["message_id"] == bridge.assistant_message_id
    assert cu["context"]["current_tokens"] == 87040
    assert cu["context"]["max_tokens"] == 128000
    assert cu["context"]["used_percentage"] == 68
    assert bridge.consume_session_context_tick() is True
    ContextMetricsRegistry.clear("sess-ctx")


def test_finish_usage_and_done() -> None:
    bridge = LangGraphSseBridge("sess-2")
    builder = AssistantMessageBuilder(session_id="sess-2", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []
    parts.extend(bridge.process_item({"type": "text-delta", "text_delta": "x"}, builder, ctx))
    parts.extend(
        bridge.process_item(
            {
                "type": "finish",
                "finish_reason": "stop",
                "usage": {"inputTokens": 3, "outputTokens": 4},
            },
            builder,
            ctx,
        )
    )
    parts.extend(bridge.finalize())
    blob = "".join(parts)
    assert "data: [DONE]" in blob
    finish_objs = [o for o in _data_json_objects(blob) if o.get("type") == "finish"]
    assert finish_objs
    fin = finish_objs[-1]
    assert fin["finish_reason"] == "stop"
    assert fin["usage"]["inputTokens"] == 3
    assert fin["usage"]["outputTokens"] == 4


def test_error_event_type() -> None:
    bridge = LangGraphSseBridge("sess-3")
    builder = AssistantMessageBuilder(session_id="sess-3", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    blob = "".join(bridge.process_item({"type": "__tw_error__", "content": "oops"}, builder, ctx))
    assert "event: error\n" in blob
    err = [o for o in _data_json_objects(blob) if o.get("type") == "error"][0]
    assert err["error"] == "操作失败，请稍后重试。"
    assert err["message_id"] == bridge.assistant_message_id


def test_phase_start_end_through_bridge() -> None:
    bridge = LangGraphSseBridge("sess-ph")
    builder = AssistantMessageBuilder(session_id="sess-ph", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []
    parts.extend(
        bridge.process_item(
            {"type": "phase-start", "phase_id": "parse_requirements", "title": "解析需求"},
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "type": "phase-delta",
                "phase_id": "parse_requirements",
                "text_delta": "上下文已就绪",
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {"type": "phase-end", "phase_id": "parse_requirements", "ok": True},
            builder,
            ctx,
        )
    )
    blob = "".join(parts)
    objs = _data_json_objects(blob)
    ps = [o for o in objs if o.get("type") == "phase-start"][0]
    assert ps["phase_id"] == "parse_requirements"
    assert ps["title"] == "解析需求"
    assert ps["message_id"] == bridge.assistant_message_id
    pd = [o for o in objs if o.get("type") == "phase-delta"][0]
    assert pd["text_delta"] == "上下文已就绪"
    assert pd["phase_id"] == "parse_requirements"
    pend = [o for o in objs if o.get("type") == "phase-end"][0]
    assert pend["phase_id"] == "parse_requirements"
    assert pend["ok"] is True


def test_tool_output_duration_ms() -> None:
    bridge = LangGraphSseBridge("sess-dur")
    builder = AssistantMessageBuilder(session_id="sess-dur", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    run_id = "run-tool-1"
    parts: List[str] = []
    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_start",
                "name": "search",
                "run_id": run_id,
                "data": {"input": {"q": "test"}},
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_end",
                "name": "search",
                "run_id": run_id,
                "data": {"output": "ok"},
            },
            builder,
            ctx,
        )
    )
    blob = "".join(parts)
    tool_out = [o for o in _data_json_objects(blob) if o.get("type") == "tool-output-available"]
    assert tool_out
    assert isinstance(tool_out[0]["duration_ms"], int)
    assert tool_out[0]["duration_ms"] >= 0
    tool_parts = [p for p in builder.to_dict()["parts"] if p.get("type") == "tool"]
    assert tool_parts[0].get("duration_ms") is not None


def test_usage_update_and_finish_cumulative() -> None:
    bridge = LangGraphSseBridge("sess-usage")
    builder = AssistantMessageBuilder(session_id="sess-usage", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []

    class _FakeOutput:
        usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}

    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_end",
                "run_id": "run-llm-1",
                "data": {"output": _FakeOutput()},
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_end",
                "run_id": "run-llm-2",
                "data": {"output": _FakeOutput()},
            },
            builder,
            ctx,
        )
    )
    parts.extend(bridge.process_item({"type": "__tw_finish__"}, builder, ctx))
    parts.extend(bridge.finalize())

    objs = _data_json_objects("".join(parts))
    usage_updates = [o for o in objs if o.get("type") == "usage-update"]
    assert len(usage_updates) == 2
    assert usage_updates[-1]["usage"]["input_tokens"] == 200
    assert usage_updates[-1]["usage"]["output_tokens"] == 100
    assert usage_updates[-1]["usage"]["total_tokens"] == 300

    finish_objs = [o for o in objs if o.get("type") == "finish"]
    assert finish_objs[-1]["usage"]["input_tokens"] == 200
    assert finish_objs[-1]["usage"]["output_tokens"] == 100


def test_usage_dedup_same_run_id() -> None:
    bridge = LangGraphSseBridge("sess-dedup")
    builder = AssistantMessageBuilder(session_id="sess-dedup", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []

    class _FakeChunk:
        content = ""
        usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    class _FakeOutput:
        usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    run_id = "run-dup-1"
    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_stream",
                "run_id": run_id,
                "data": {"chunk": _FakeChunk()},
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_end",
                "run_id": run_id,
                "data": {"output": _FakeOutput()},
            },
            builder,
            ctx,
        )
    )

    objs = _data_json_objects("".join(parts))
    usage_updates = [o for o in objs if o.get("type") == "usage-update"]
    assert len(usage_updates) == 1


def test_chat_model_end_flushes_unstreamed_text() -> None:
    """非流式或空 chunk 场景：终态 AIMessage 正文须在 on_chat_model_end 补发 text-delta。"""
    bridge = LangGraphSseBridge("sess-end-text")
    builder = AssistantMessageBuilder(session_id="sess-end-text", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []

    class _FakeOutput:
        content = "你好！有什么可以帮助你的吗？"
        usage_metadata = {"input_tokens": 706, "output_tokens": 22, "total_tokens": 728}

    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_end",
                "run_id": "run-end-1",
                "data": {"output": _FakeOutput()},
            },
            builder,
            ctx,
        )
    )
    parts.extend(bridge.process_item({"type": "__tw_finish__"}, builder, ctx))
    parts.extend(bridge.finalize())

    objs = _data_json_objects("".join(parts))
    td = [o for o in objs if o.get("type") == "text-delta"]
    assert td
    assert td[0]["text_delta"] == "你好！有什么可以帮助你的吗？"
    assert ctx["text_buffer"] == ""
    text_parts = [p for p in builder.to_dict()["parts"] if p.get("type") == "text"]
    assert text_parts
    assert "你好！有什么可以帮助你的吗？" in text_parts[0]["content"]


def test_chat_model_end_does_not_duplicate_streamed_text() -> None:
    bridge = LangGraphSseBridge("sess-end-dedup")
    builder = AssistantMessageBuilder(session_id="sess-end-dedup", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []

    class _StreamChunk:
        content = "你好"
        additional_kwargs = {}

    class _FakeOutput:
        content = "你好世界"
        usage_metadata = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}

    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_stream",
                "run_id": "run-dedup",
                "data": {"chunk": _StreamChunk()},
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_end",
                "run_id": "run-dedup",
                "data": {"output": _FakeOutput()},
            },
            builder,
            ctx,
        )
    )

    objs = _data_json_objects("".join(parts))
    td = [o for o in objs if o.get("type") == "text-delta"]
    assert [o["text_delta"] for o in td] == ["你好", "世界"]


def test_reasoning_stream_then_text_closes_reasoning() -> None:
    bridge = LangGraphSseBridge("sess-reason")
    builder = AssistantMessageBuilder(session_id="sess-reason", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []

    class _ReasonChunk:
        content = ""
        additional_kwargs = {"reasoning_content": "think-a"}

    class _TextChunk:
        content = "answer"
        additional_kwargs = {}

    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_stream",
                "run_id": "run-r1",
                "data": {"chunk": _ReasonChunk()},
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "event": "on_chat_model_stream",
                "run_id": "run-r1",
                "data": {"chunk": _TextChunk()},
            },
            builder,
            ctx,
        )
    )
    blob = "".join(parts)
    objs = _data_json_objects(blob)
    types = [o["type"] for o in objs]
    assert "reasoning-start" in types
    rd = [o for o in objs if o["type"] == "reasoning-delta"]
    assert rd and rd[0]["text_delta"] == "think-a"
    re_idx = types.index("reasoning-end")
    td_idx = types.index("text-delta")
    assert re_idx < td_idx
    assert builder._content.parts  # noqa: SLF001 — 测试累积
    assert any(getattr(p, "type", None) == "reasoning" for p in builder._content.parts)  # noqa: SLF001


def test_subagent_child_tool_gets_parent_task_call_id() -> None:
    bridge = LangGraphSseBridge("sess-sub")
    builder = AssistantMessageBuilder(session_id="sess-sub", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    task_run = "run-task-1"
    child_run = "run-read-1"

    parts: List[str] = []
    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_start",
                "name": TASK_TOOL_NAME,
                "run_id": task_run,
                "parent_ids": [],
                "data": {
                    "input": {
                        "description": "检索日志",
                        "subagent_type": "general-purpose",
                        "prompt": "find errors",
                    },
                },
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_start",
                "name": "read",
                "run_id": child_run,
                "parent_ids": [task_run],
                "data": {"input": {"path": "/var/log/nginx/error.log"}},
            },
            builder,
            ctx,
        )
    )
    objs = _data_json_objects("".join(parts))
    avail = [o for o in objs if o["type"] == "tool-input-available"]
    task_avail = next(o for o in avail if o["name"] == TASK_TOOL_NAME)
    read_avail = next(o for o in avail if o["name"] == "read")
    assert "parent_task_call_id" not in task_avail
    assert read_avail.get("parent_task_call_id") == task_avail["tool_call_id"]

    tool_parts = [p for p in builder._content.parts if isinstance(p, ToolPart)]  # noqa: SLF001
    read_part = next(p for p in tool_parts if p.name == "read")
    assert read_part.parent_task_call_id == task_avail["tool_call_id"]

    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_end",
                "name": TASK_TOOL_NAME,
                "run_id": task_run,
                "data": {"output": "Task Succeeded. Result: ok"},
            },
            builder,
            ctx,
        )
    )
    assert ctx["task_tool_call_stack"] == []


def test_subagent_text_delta_gets_parent_task_call_id() -> None:
    bridge = LangGraphSseBridge("sess-sub-text")
    builder = AssistantMessageBuilder(
        session_id="sess-sub-text",
        message_id=bridge.assistant_message_id,
    )
    ctx = _ctx()
    task_run = "run-task-text"
    llm_run = "run-llm-sub"

    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": TASK_TOOL_NAME,
            "run_id": task_run,
            "parent_ids": [],
            "data": {"input": {"description": "调试", "prompt": "test"}},
        },
        builder,
        ctx,
    )

    class _TextChunk:
        content = "好的，我先检查根目录。"

    text = "".join(
        bridge.process_item(
            {
                "event": "on_chat_model_stream",
                "name": "ChatOpenAI",
                "run_id": llm_run,
                "parent_ids": [task_run],
                "data": {"chunk": _TextChunk()},
            },
            builder,
            ctx,
        )
    )
    objs = _data_json_objects(text)
    td = [o for o in objs if o["type"] == "text-delta"]
    assert td
    task_tc = ctx["run_id_to_tool_call_id"][task_run]
    assert td[0].get("parent_task_call_id") == task_tc
    assert ctx.get("text_buffer")
    assert ctx.get("text_buffer_parent_task_call_id") == task_tc


def test_parallel_tasks_parent_task_call_id_not_cross_wired() -> None:
    bridge = LangGraphSseBridge("sess-par")
    builder = AssistantMessageBuilder(session_id="sess-par", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    run_a, run_b = "run-task-a", "run-task-b"
    run_child_b = "run-read-b"

    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": TASK_TOOL_NAME,
            "run_id": run_a,
            "parent_ids": [],
            "data": {"input": {"description": "任务 A", "prompt": "a"}},
        },
        builder,
        ctx,
    )
    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": TASK_TOOL_NAME,
            "run_id": run_b,
            "parent_ids": [],
            "data": {"input": {"description": "任务 B", "prompt": "b"}},
        },
        builder,
        ctx,
    )
    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": "read",
            "run_id": run_child_b,
            "parent_ids": [run_b],
            "data": {"input": {"path": "/b"}},
        },
        builder,
        ctx,
    )
    serialized = builder.serialize()
    assert '"parent_task_call_id"' in serialized
    import json as _json

    parts = _json.loads(serialized)["parts"]
    read_saved = next(p for p in parts if p.get("name") == "read")
    task_b = next(
        p for p in parts
        if p.get("name") == TASK_TOOL_NAME and p.get("input", {}).get("description") == "任务 B"
    )
    assert read_saved["parent_task_call_id"] == task_b["tool_call_id"]


def test_bridge_raw_to_sse_lines_skips_end_sentinel() -> None:
    bridge = LangGraphSseBridge("sess-sentinel")
    ctx = _ctx()
    assert bridge_raw_to_sse_lines(
        END_SENTINEL, bridge, None, ctx, keepalive_comment=": keepalive\n\n",
    ) is None
    assert bridge_raw_to_sse_lines(
        HEARTBEAT_SENTINEL, bridge, None, ctx, keepalive_comment=": keepalive\n\n",
    ) == [": keepalive\n\n"]


def test_execute_nonzero_exit_is_projected_as_tool_error() -> None:
    bridge = LangGraphSseBridge("sess-exit")
    builder = AssistantMessageBuilder(session_id="sess-exit", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": "execute",
            "run_id": "run-exec",
            "data": {"input": {"command": "false"}},
        },
        builder,
        ctx,
    )

    class _FailedOutput:
        status = "success"
        content = "\n[Command failed with exit code 1]"

    lines = bridge.process_item(
        {
            "event": "on_tool_end",
            "name": "execute",
            "run_id": "run-exec",
            "data": {"output": _FailedOutput()},
        },
        builder,
        ctx,
    )
    events = _data_json_objects("".join(lines))
    tool_output = next(item for item in events if item["type"] == "tool-output-available")
    assert tool_output["status"] == "success"
    assert tool_output["state"] == "failed"
    assert tool_output["outcome"] == "command_failed"
    assert tool_output["exit_code"] == 1
    assert tool_output["errorCategory"] == "command_failed"
    saved = next(part for part in builder.to_dict()["parts"] if part.get("name") == "execute")
    assert saved["status"] == "success"
    assert saved["state"] == "failed"


@pytest.mark.parametrize(
    ("content", "expected_state", "expected_outcome"),
    [
        ("Error: Command timed out\n[Command failed with exit code 124]", "timed_out", "timed_out"),
        ("the word failed is ordinary output\n[Command succeeded with exit code 0]", "succeeded", "ok"),
        ("optional command failed but shell recovered\n[Command succeeded with exit code 0]", "succeeded", "ok"),
    ],
)
def test_execute_uses_exit_protocol_not_output_words(
    content: str,
    expected_state: str,
    expected_outcome: str,
) -> None:
    bridge = LangGraphSseBridge("sess-exit-protocol")
    builder = AssistantMessageBuilder()
    ctx = _ctx()
    bridge.process_item({
        "event": "on_tool_start",
        "name": "execute",
        "run_id": "run-exec-protocol",
        "data": {"input": {"command": "command"}},
    }, builder, ctx)

    class _Output:
        status = "success"

        def __init__(self, value: str) -> None:
            self.content = value

    lines = bridge.process_item({
        "event": "on_tool_end",
        "name": "execute",
        "run_id": "run-exec-protocol",
        "data": {"output": _Output(content)},
    }, builder, ctx)
    event = next(
        item for item in _data_json_objects("".join(lines))
        if item["type"] == "tool-output-available"
    )
    assert event["state"] == expected_state
    assert event["outcome"] == expected_outcome


def test_abort_with_error_reason_emits_error_without_success_finish() -> None:
    bridge = LangGraphSseBridge("sess-abort-error")
    builder = AssistantMessageBuilder()
    ctx = _ctx()
    first = bridge.process_item(
        {"type": "abort", "finish_reason": "error", "content": "internal stack"},
        builder,
        ctx,
    )
    final = bridge.finalize()
    blob = "".join(first + final)
    assert "event: error" in blob
    assert "event: finish" not in blob
    assert "data: [DONE]" in blob


def test_tool_error_uses_inflight_tool_call_id() -> None:
    """on_tool_error 的 tool_call_id 应与 tool-input 一致，避免前端出现孤儿工具块。"""
    bridge = LangGraphSseBridge("sess-tool-err")
    builder = AssistantMessageBuilder(session_id="sess-tool-err", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    model_call_id = "019eaab7-3401-79d0-bdea-59f545d0f087"
    mcp_call_id = "call_27021b357bb0419f9b65d3d3"

    parts: list[str] = []
    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_start",
                "name": "bash",
                "run_id": "run-start",
                "data": {"input": {"command": "uptime", "ip": "192.0.2.1"}, "tool_call_id": model_call_id},
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_error",
                "name": "bash",
                "run_id": "run-err",
                "data": {
                    "error": ToolInfrastructureError(
                        "[INTERNAL_ERROR] Docker image ubuntu:latest not found"
                    ),
                    "tool_call_id": mcp_call_id,
                },
            },
            builder,
            ctx,
        )
    )
    objs = _data_json_objects("".join(parts))
    outputs = [o for o in objs if o.get("type") == "tool-output-available"]
    assert len(outputs) == 1
    assert outputs[0]["tool_call_id"] == model_call_id
    assert outputs[0]["status"] == "error"
    assert outputs[0]["error"] == "环境不可用"
    assert outputs[0]["errorCategory"] == "infrastructure"

    saved = builder.to_dict()["parts"][0]
    assert saved["tool_call_id"] == model_call_id
    assert saved["status"] == "error"
    assert saved["error"]
    assert "toolCallId" not in saved
    assert "durationMs" not in saved
    assert saved.get("errorCategory") == "infrastructure"


def test_tool_output_error_frame_golden_fields() -> None:
    """error 帧须携带固定用户短句与 errorCategory。"""
    bridge = LangGraphSseBridge("sess-err-golden")
    builder = AssistantMessageBuilder(
        session_id="sess-err-golden",
        message_id=bridge.assistant_message_id,
    )
    ctx = _ctx()
    run_id = "run-timeout"

    class _ErrOutput:
        content = "command timed out after 30s"
        status = "error"

    parts: list[str] = []
    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_start",
                "name": "bash",
                "run_id": run_id,
                "data": {"input": {"command": "sleep 99"}},
            },
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {
                "event": "on_tool_end",
                "name": "bash",
                "run_id": run_id,
                "data": {"output": _ErrOutput()},
            },
            builder,
            ctx,
        )
    )
    out = [o for o in _data_json_objects("".join(parts)) if o.get("type") == "tool-output-available"][-1]
    assert out["status"] == "error"
    assert out["error"] == "执行失败"
    assert out["errorCategory"] == "unknown"
    assert out["output"] == ""

    saved = builder.to_dict()["parts"][0]
    assert saved["error"] == "执行失败"
    assert saved["errorCategory"] == "unknown"


def test_task_tool_subagent_child_error_maps_subagent_failure() -> None:
    """子图含 error tool 时，task 结束应标 subagent_failure（不依赖 Task failed. 前缀）。"""
    bridge = LangGraphSseBridge("sess-task-child-err")
    builder = AssistantMessageBuilder(
        session_id="sess-task-child-err",
        message_id=bridge.assistant_message_id,
    )
    ctx = _ctx()
    task_run = "run-task-child"
    child_run = "run-bash-child"

    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": TASK_TOOL_NAME,
            "run_id": task_run,
            "data": {"input": {"description": "子任务", "prompt": "x"}},
        },
        builder,
        ctx,
    )
    task_tc = ctx["run_id_to_tool_call_id"][task_run]
    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": "bash",
            "run_id": child_run,
            "parent_ids": [task_run],
            "data": {"input": {"command": "false"}},
        },
        builder,
        ctx,
    )
    bridge.process_item(
        {
            "event": "on_tool_error",
            "name": "bash",
            "run_id": child_run,
            "parent_ids": [task_run],
            "data": {"error": RuntimeError("child broke")},
        },
        builder,
        ctx,
    )
    blob = "".join(
        bridge.process_item(
            {
                "event": "on_tool_end",
                "name": TASK_TOOL_NAME,
                "run_id": task_run,
                "data": {"output": "Task Succeeded. Result: partial"},
            },
            builder,
            ctx,
        )
    )
    out = [o for o in _data_json_objects(blob) if o.get("type") == "tool-output-available"][-1]
    assert out["status"] == "error"
    assert out["errorCategory"] == "subagent_failure"
    assert out["error"] == "执行失败"


def test_task_tool_failed_maps_subagent_failure() -> None:
    bridge = LangGraphSseBridge("sess-task-fail")
    builder = AssistantMessageBuilder(
        session_id="sess-task-fail",
        message_id=bridge.assistant_message_id,
    )
    ctx = _ctx()
    task_run = "run-task-fail"

    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": TASK_TOOL_NAME,
            "run_id": task_run,
            "data": {"input": {"description": "子任务", "prompt": "x"}},
        },
        builder,
        ctx,
    )
    blob = "".join(
        bridge.process_item(
            {
                "event": "on_tool_end",
                "name": TASK_TOOL_NAME,
                "run_id": task_run,
                "data": {"output": "Task failed. bash: connection refused"},
            },
            builder,
            ctx,
        )
    )
    out = [o for o in _data_json_objects(blob) if o.get("type") == "tool-output-available"][-1]
    assert out["status"] == "error"
    assert out["errorCategory"] == "subagent_failure"
    assert out["error"] == "执行失败"


def test_reasoning_disabled_when_show_thinking_off() -> None:
    bridge = LangGraphSseBridge("sess-no-think")
    bridge._show_thinking = False
    builder = AssistantMessageBuilder(session_id="sess-no-think", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _ReasonChunk:
        content = ""
        additional_kwargs = {"reasoning_content": "hidden"}

    text = "".join(
        bridge.process_item(
            {
                "event": "on_chat_model_stream",
                "run_id": "run-x",
                "data": {"chunk": _ReasonChunk()},
            },
            builder,
            ctx,
        )
    )
    objs = _data_json_objects(text)
    assert not any(o.get("type", "").startswith("reasoning") for o in objs)
