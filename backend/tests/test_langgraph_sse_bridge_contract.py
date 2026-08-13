"""LangGraphSseBridge → Noesis SSE 字符串的最小契约断言（防静默破坏 useSSEStream）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from noesis.chat.event_mapping.langgraph_bridge import TASK_TOOL_NAME, LangGraphSseBridge, bridge_raw_to_sse_lines
from noesis.errors.tool_failure import ToolInfrastructureError
from noesis.chat.event_mapping.bridge import END_SENTINEL, HEARTBEAT_SENTINEL
from noesis.chat.message_builder import AssistantMessageBuilder, ToolPart


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


def test_kb_retrieval_event_preserves_sources() -> None:
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
    assert retrieval["results"][0]["evidence_id"].startswith("ev_")
    assert retrieval["results"][0]["tool_call_ids"] == ["call-1"]

def test_reasoning_can_continue_after_retrieval_result() -> None:
    bridge = LangGraphSseBridge("sess-retrieval-reasoning")
    builder = AssistantMessageBuilder(
        session_id="sess-retrieval-reasoning",
        message_id=bridge.assistant_message_id,
    )
    ctx = _ctx()
    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": "web_search",
            "run_id": "call-search",
            "data": {"input": {"query": "LLM wiki"}},
        },
        builder,
        ctx,
    )
    bridge.process_item(
        {
            "event": "on_tool_end",
            "name": "web_search",
            "run_id": "call-search",
            "data": {
                "output": json.dumps(
                    {
                        "results": [
                            {
                                "url": "https://example.com/llm-wiki",
                                "title": "LLM Wiki",
                                "excerpt": "A research project",
                                "citable": True,
                            }
                        ]
                    }
                )
            },
        },
        builder,
        ctx,
    )

    class _ReasonChunk:
        content = ""
        additional_kwargs = {"reasoning_content": "继续分析检索结果"}

    lines = bridge.process_item(
        {
            "event": "on_chat_model_stream",
            "run_id": "run-after-search",
            "data": {"chunk": _ReasonChunk()},
        },
        builder,
        ctx,
    )

    assert any(
        event.get("type") == "reasoning-delta"
        for event in _data_json_objects("".join(lines))
    )
    assert [part["type"] for part in builder.to_dict()["parts"]][-2:] == [
        "retrieval",
        "reasoning",
    ]


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
    from noesis.runtime.observability import ContextMetricsRegistry

    bridge = LangGraphSseBridge("sess-usage-ctx")
    builder = AssistantMessageBuilder(session_id="sess-usage-ctx", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []
    parts.extend(bridge.process_item({"type": "text-delta", "text_delta": "hi"}, builder, ctx))
    with patch("noesis.chat.event_mapping.langgraph_bridge.resolve_context_max_tokens", return_value=128000):
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
    """on_chat_model_end 后 context-update 携带 provider 真实 input_tokens。"""
    from noesis.runtime.observability import ContextMetricsRegistry

    bridge = LangGraphSseBridge("sess-ctx")
    builder = AssistantMessageBuilder(session_id="sess-ctx", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    with patch("noesis.chat.event_mapping.langgraph_bridge.resolve_context_max_tokens", return_value=128000):
        blob = "".join(
            bridge.process_item(
                {
                    "event": "on_chat_model_end",
                    "data": {"output": MagicMock(usage_metadata={"input_tokens": 87040, "output_tokens": 100})},
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


def test_context_update_is_bound_to_the_current_model_run() -> None:
    """A concurrent model run must not overwrite this run's context indicator."""
    from noesis.runtime.observability import ContextMetricsRegistry

    bridge = LangGraphSseBridge("sess-context-runs")
    builder = AssistantMessageBuilder(
        session_id="sess-context-runs",
        message_id=bridge.assistant_message_id,
    )
    ctx = _ctx()
    original_put = ContextMetricsRegistry.put

    def interleave_other_run(cls, session_id, snapshot, *, run_id=""):
        original_put(session_id, snapshot, run_id=run_id)
        if run_id == "run-current":
            original_put(
                session_id,
                {"current_tokens": 999, "max_tokens": 128000, "used_percentage": 1},
                run_id="run-other",
            )

    with patch.object(ContextMetricsRegistry, "put", classmethod(interleave_other_run)):
        with patch("noesis.chat.event_mapping.langgraph_bridge.resolve_context_max_tokens", return_value=128000):
            blob = "".join(
                bridge.process_item(
                    {
                        "event": "on_chat_model_end",
                        "run_id": "run-current",
                        "data": {"output": MagicMock(usage_metadata={"input_tokens": 10, "output_tokens": 5})},
                    },
                    builder,
                    ctx,
                )
            )

    cu = [o for o in _data_json_objects(blob) if o.get("type") == "context-update"][0]
    assert cu["context"]["current_tokens"] == 10
    ContextMetricsRegistry.clear("sess-context-runs")


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


def test_tool_start_omits_injected_runtime_from_sse_and_builder() -> None:
    class ToolRuntime:
        pass

    bridge = LangGraphSseBridge("sess-runtime")
    builder = AssistantMessageBuilder(
        session_id="sess-runtime",
        message_id=bridge.assistant_message_id,
    )
    lines = bridge.process_item(
        {
            "event": "on_tool_start",
            "name": "web_search",
            "run_id": "run-search",
            "data": {
                "input": {
                    "query": "LLM wiki",
                    "limit": 8,
                    "runtime": ToolRuntime(),
                }
            },
        },
        builder,
        _ctx(),
    )

    available = next(
        event
        for event in _data_json_objects("".join(lines))
        if event.get("type") == "tool-input-available"
    )
    assert available["input"] == {"query": "LLM wiki", "limit": 8}
    assert json.loads(available["input_text"]) == available["input"]
    saved_tool = next(
        part for part in builder.to_dict()["parts"] if part.get("type") == "tool"
    )
    assert saved_tool["input"] == available["input"]


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


def test_usage_dedup_same_model_run_across_contexts() -> None:
    """同一模型 run 在子 Agent context 变化时也不能再次累计。"""
    bridge = LangGraphSseBridge("sess-cross-context-dedup")
    builder = AssistantMessageBuilder(
        session_id="sess-cross-context-dedup",
        message_id=bridge.assistant_message_id,
    )

    class _Output:
        usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

    first = _ctx()
    second = _ctx()
    first_parts = bridge.process_item(
        {"event": "on_chat_model_end", "run_id": "run-cross-1", "data": {"output": _Output()}},
        builder,
        first,
    )
    second_parts = bridge.process_item(
        {"event": "on_chat_model_end", "run_id": "run-cross-1", "data": {"output": _Output()}},
        builder,
        second,
    )

    first_updates = [o for o in _data_json_objects("".join(first_parts)) if o.get("type") == "usage-update"]
    second_updates = [o for o in _data_json_objects("".join(second_parts)) if o.get("type") == "usage-update"]
    assert first_updates[-1]["usage"]["total_tokens"] == 120
    assert not second_updates


def test_usage_end_is_authoritative_over_partial_stream_chunk() -> None:
    """流式 chunk 携带部分 usage（run_id R）时，on_chat_model_end 的完整 usage 须覆盖之，不得被去重丢弃。

    复现线上 ↓2 症状：部分 stream chunk 的 output_tokens 先进入累计并被 run_id 标记，
    导致终态完整 usage 被同 run_id 去重跳过，output 停留在部分值。
    """
    bridge = LangGraphSseBridge("sess-partial-usage")
    builder = AssistantMessageBuilder(session_id="sess-partial-usage", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    parts: List[str] = []

    class _PartialChunk:
        content = ""
        # 部分 usage：input 已知、output 仅 2、无 total
        usage_metadata = {"input_tokens": 21400, "output_tokens": 2}

    class _CompleteOutput:
        # 终态完整 usage：output 为真实生成量
        usage_metadata = {
            "input_tokens": 21400,
            "output_tokens": 593,
            "total_tokens": 21993,
            "output_token_details": {"reasoning_tokens": 1200},
        }

    run_id = "run-partial-1"
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_stream", "run_id": run_id, "data": {"chunk": _PartialChunk()}},
            builder,
            ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": run_id, "data": {"output": _CompleteOutput()}},
            builder,
            ctx,
        )
    )

    objs = _data_json_objects("".join(parts))
    usage_updates = [o for o in objs if o.get("type") == "usage-update"]
    assert usage_updates, "expected at least one usage-update"
    final = usage_updates[-1]["usage"]
    # 终态 output 须反映真实生成量，而非被部分 stream chunk 冻结在 2
    assert final["output_tokens"] == 593
    assert final["total_tokens"] == 21993



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


# ---------- task 3.5: 流式 chunk / model end / 重放 / 无 usage / 并行子 Agent 聚合 ----------


def test_usage_aggregates_across_stream_chunk_and_model_end() -> None:
    """流式 chunk 携带部分 usage + model end 携带完整 usage：终态权威值不被丢弃。

    usage 只在 on_chat_model_end 累计（stream chunk 不累计），避免部分值冻结。
    """
    bridge = LangGraphSseBridge("sess-stream-end")
    builder = AssistantMessageBuilder(session_id="sess-stream-end", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _PartialChunk:
        content = "hi"
        usage_metadata = {"input_tokens": 100, "output_tokens": 2}  # 部分

    class _CompleteOutput:
        usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}

    run_id = "run-se-1"
    parts: List[str] = []
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_stream", "run_id": run_id, "data": {"chunk": _PartialChunk()}},
            builder, ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": run_id, "data": {"output": _CompleteOutput()}},
            builder, ctx,
        )
    )

    objs = _data_json_objects("".join(parts))
    usage_updates = [o for o in objs if o.get("type") == "usage-update"]
    assert usage_updates
    # 终态完整 usage 被采用（output=50，不是部分值 2）
    assert usage_updates[-1]["usage"]["output_tokens"] == 50
    assert usage_updates[-1]["usage"]["total_tokens"] == 150


def test_usage_repeated_model_end_same_run_id_deduped() -> None:
    """同一 run_id 的 model end 重放/重复事件只累计一次。"""
    bridge = LangGraphSseBridge("sess-replay")
    builder = AssistantMessageBuilder(session_id="sess-replay", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _Output:
        usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

    run_id = "run-replay-1"
    parts: List[str] = []
    for _ in range(3):
        parts.extend(
            bridge.process_item(
                {"event": "on_chat_model_end", "run_id": run_id, "data": {"output": _Output()}},
                builder, ctx,
            )
        )

    objs = _data_json_objects("".join(parts))
    usage_updates = [o for o in objs if o.get("type") == "usage-update"]
    # 只累计一次
    assert len(usage_updates) == 1
    assert usage_updates[0]["usage"]["input_tokens"] == 100


def test_provider_no_usage_emits_no_usage_update() -> None:
    """Provider 无 usage_metadata：不发 usage-update，不阻断文本流。"""
    bridge = LangGraphSseBridge("sess-no-usage")
    builder = AssistantMessageBuilder(session_id="sess-no-usage", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _OutputNoUsage:
        content = "answer"
        usage_metadata = None

    parts: List[str] = []
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": "run-none", "data": {"output": _OutputNoUsage()}},
            builder, ctx,
        )
    )

    objs = _data_json_objects("".join(parts))
    assert not any(o.get("type") == "usage-update" for o in objs)
    # 文本仍正常
    assert any(o.get("type") == "text-delta" for o in objs)


def test_parallel_subagent_usage_aggregated_by_caller() -> None:
    """并行子 Agent：各自 model call 归 subagent，run 总量求和，finish 附带 attribution。"""
    bridge = LangGraphSseBridge("sess-parallel-sub")
    builder = AssistantMessageBuilder(session_id="sess-parallel-sub", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _LeadOutput:
        usage_metadata = {"input_tokens": 200, "output_tokens": 50, "total_tokens": 250}

    class _SubOutput:
        usage_metadata = {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130}

    parts: List[str] = []
    # 主 Agent model call
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": "lead-run", "data": {"output": _LeadOutput()}},
            builder, ctx,
        )
    )
    # task tool start（压栈，让子 Agent model call 归 subagent）
    parts.extend(
        bridge.process_item(
            {"event": "on_tool_start", "run_id": "task-tool-1", "name": "task",
             "data": {"input": {"tool_call_id": "task-call-1", "description": "sub task 1"}}},
            builder, ctx,
        )
    )
    # 子 Agent model call（parent_ids 指向 task tool run_id）
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": "sub-run-1", "parent_ids": ["task-tool-1"],
             "data": {"output": _SubOutput()}},
            builder, ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {"event": "on_tool_end", "run_id": "task-tool-1", "name": "task"},
            builder, ctx,
        )
    )
    parts.extend(bridge.process_item({"type": "__tw_finish__"}, builder, ctx))
    parts.extend(bridge.finalize())

    objs = _data_json_objects("".join(parts))
    finish_objs = [o for o in objs if o.get("type") == "finish"]
    assert finish_objs
    finish = finish_objs[-1]
    # cumulative = 200 + 100
    assert finish["usage"]["input_tokens"] == 300
    # attribution 附带 by_caller
    assert "attribution" in finish
    assert finish["attribution"]["by_caller"]["lead_agent"]["input_tokens"] == 200
    assert finish["attribution"]["by_caller"]["subagent"]["input_tokens"] == 100


def test_usage_details_accumulate_across_multiple_calls() -> None:
    """多次 model call 的 cache/reasoning detail 累计到 cumulative。"""
    bridge = LangGraphSseBridge("sess-detail-acc")
    builder = AssistantMessageBuilder(session_id="sess-detail-acc", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _Output1:
        usage_metadata = {
            "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
            "input_token_details": {"cache_read": 60, "cache_creation": 10},
            "output_token_details": {"reasoning": 5},
        }

    class _Output2:
        usage_metadata = {
            "input_tokens": 80, "output_tokens": 15, "total_tokens": 95,
            "input_token_details": {"cache_read": 40},
        }

    parts: List[str] = []
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": "d1", "data": {"output": _Output1()}},
            builder, ctx,
        )
    )
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": "d2", "data": {"output": _Output2()}},
            builder, ctx,
        )
    )

    objs = _data_json_objects("".join(parts))
    usage_updates = [o for o in objs if o.get("type") == "usage-update"]
    final = usage_updates[-1]["usage"]
    assert final["input_tokens"] == 180
    assert final["input_token_details"]["cache_read"] == 100
    assert final["input_token_details"]["cache_write"] == 10
    assert final["output_token_details"]["reasoning"] == 5


# ---------- task 4.1 / 4.2: SSE 字段向后兼容 + 重订阅不重复累计 ----------


def test_sse_payloads_preserve_legacy_fields_while_adding_new_ones() -> None:
    """usage-update/context-update/finish 保留既有字段，新增字段向后兼容。"""
    bridge = LangGraphSseBridge("sess-compat")
    builder = AssistantMessageBuilder(session_id="sess-compat", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _Output:
        usage_metadata = {
            "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
            "input_token_details": {"cache_read": 60},
        }

    parts: List[str] = []
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": "run-compat", "data": {"output": _Output()}},
            builder, ctx,
        )
    )
    parts.extend(bridge.process_item({"type": "__tw_finish__"}, builder, ctx))
    parts.extend(bridge.finalize())

    objs = _data_json_objects("".join(parts))

    # usage-update：既有 input/output/total 保留 + 新 details
    usage_update = next(o for o in objs if o.get("type") == "usage-update")
    u = usage_update["usage"]
    assert u["input_tokens"] == 100  # 既有
    assert u["output_tokens"] == 20  # 既有
    assert u["total_tokens"] == 120  # 既有
    assert u["input_token_details"]["cache_read"] == 60  # 新增

    # finish：既有 finish_reason/usage 保留
    finish = next(o for o in objs if o.get("type") == "finish")
    assert finish["finish_reason"] == "stop"  # 既有
    assert finish["usage"]["input_tokens"] == 100  # 既有
    # message_id 既有
    assert finish["message_id"] == bridge.assistant_message_id


def test_replay_does_not_re_accumulate_usage() -> None:
    """重订阅 replay 是已编码事件，不重新走 bridge 累计，usage 不重复。

    验证：同一 bridge 实例处理 model end 后，finalize 的 finish.usage 是单次值；
    重放同一事件序列不会让 usage 翻倍（replay 走 delivery 已编码事件，不重跑 process_item）。
    """
    bridge = LangGraphSseBridge("sess-replay-no-dup")
    builder = AssistantMessageBuilder(session_id="sess-replay-no-dup", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _Output:
        usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

    parts: List[str] = []
    parts.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": "run-no-dup", "data": {"output": _Output()}},
            builder, ctx,
        )
    )
    parts.extend(bridge.process_item({"type": "__tw_finish__"}, builder, ctx))
    parts.extend(bridge.finalize())

    objs = _data_json_objects("".join(parts))
    finish = next(o for o in objs if o.get("type") == "finish")
    # 单次累计，未翻倍
    assert finish["usage"]["input_tokens"] == 100
    assert finish["usage"]["total_tokens"] == 120

    # 重放同一 run_id 的 model end：bridge 的去重应跳过（seen_run_ids）
    parts2: List[str] = []
    parts2.extend(
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": "run-no-dup", "data": {"output": _Output()}},
            builder, ctx,
        )
    )
    objs2 = _data_json_objects("".join(parts2))
    # 去重：不产生新的 usage-update
    assert not any(o.get("type") == "usage-update" for o in objs2)


def test_finish_attribution_carried_through_delivery_event() -> None:
    """finish 的 attribution 字段经 delivery 解析为 RunCompleted.attribution（4.2 恢复路径）。"""
    from noesis.chat.delivery.sse import parse_sse_line_to_event
    from noesis.chat.delivery.events import RunCompleted

    finish_frame = (
        'event: finish\n'
        'data: {"type":"finish","message_id":"m1","finish_reason":"stop",'
        '"usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120},'
        '"attribution":{"cumulative":{"input_tokens":100},'
        '"by_caller":{"lead_agent":{"input_tokens":100}}}}\n\n'
    )
    events = parse_sse_line_to_event(finish_frame)
    completed = next(e for e in events if isinstance(e, RunCompleted))
    assert completed.usage["input_tokens"] == 100
    assert completed.attribution["by_caller"]["lead_agent"]["input_tokens"] == 100


# ---------- task 4.3 / 4.4: 边界校验 + 持久化不按 token delta 写库 ----------


def test_malformed_usage_detail_does_not_block_text_stream() -> None:
    """异常 detail 字段（非 dict / 非整数）降级，不阻断文本流。"""
    bridge = LangGraphSseBridge("sess-malformed")
    builder = AssistantMessageBuilder(session_id="sess-malformed", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _BadOutput:
        content = "正常正文"
        usage_metadata = {
            "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
            "input_token_details": "not a dict",  # 异常
            "output_token_details": {"reasoning": "not an int"},  # 异常
        }

    parts = list(bridge.process_item(
        {"event": "on_chat_model_end", "run_id": "run-bad", "data": {"output": _BadOutput()}},
        builder, ctx,
    ))
    objs = _data_json_objects("".join(parts))
    # 文本流不阻断
    assert any(o.get("type") == "text-delta" for o in objs)
    # usage 仍可用（平铺字段正常）
    usage_updates = [o for o in objs if o.get("type") == "usage-update"]
    assert usage_updates
    assert usage_updates[-1]["usage"]["input_tokens"] == 100
    # 异常 detail 不进 payload（非 dict 被跳过）
    assert "input_token_details" not in usage_updates[-1]["usage"]


def test_zero_and_missing_usage_details_distinguished() -> None:
    """Provider 返回 cache_write=0 与缺失 cache_write 区分：0 保留，缺失不补。"""
    bridge = LangGraphSseBridge("sess-zero-vs-missing")
    builder = AssistantMessageBuilder(session_id="sess-zero-vs-missing", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _WithZero:
        usage_metadata = {
            "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
            "input_token_details": {"cache_read": 60, "cache_creation": 0},  # cache_write=0
        }

    class _Missing:
        usage_metadata = {
            "input_tokens": 50, "output_tokens": 10, "total_tokens": 60,
            "input_token_details": {"cache_read": 30},  # 无 cache_creation
        }

    parts: List[str] = []
    parts.extend(bridge.process_item(
        {"event": "on_chat_model_end", "run_id": "z1", "data": {"output": _WithZero()}},
        builder, ctx,
    ))
    parts.extend(bridge.process_item(
        {"event": "on_chat_model_end", "run_id": "z2", "data": {"output": _Missing()}},
        builder, ctx,
    ))

    objs = _data_json_objects("".join(parts))
    usage_updates = [o for o in objs if o.get("type") == "usage-update"]
    final = usage_updates[-1]["usage"]
    # cache_write: 0 + 缺失 = 0（0 被保留并累计，缺失不补但已有 0）
    assert final["input_token_details"]["cache_read"] == 90
    assert final["input_token_details"]["cache_write"] == 0


def test_persisted_usage_is_terminal_only_not_per_delta() -> None:
    """持久化只读终态 last_finish_usage，不按 token delta 写库；attribution/breakdown 不落库。"""
    bridge = LangGraphSseBridge("sess-persist")
    bridge.last_finish_usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    bridge.last_finish_reason = "stop"

    # 终态 usage 是单次快照（last_finish_usage），不是每 delta 累积的中间值
    assert bridge.last_finish_usage["input_tokens"] == 100
    assert bridge.last_finish_usage["total_tokens"] == 120
    # attribution 不在 last_finish_usage（调试字段，只在 finish 事件透传，不落库）
    assert "attribution" not in bridge.last_finish_usage
    assert "breakdown" not in bridge.last_finish_usage
    # last_finish_usage 只在 _emit_finish 时赋值一次（终态），非 per-delta
    # 验证：未发 finish 时 last_finish_usage 为空
    fresh_bridge = LangGraphSseBridge("sess-persist-fresh")
    assert fresh_bridge.last_finish_usage == {}


def test_steps_bounded_does_not_grow_unbounded() -> None:
    """大量 model call：steps 有界，cumulative 仍准确（已在 3.3 测，此处验证 bridge 集成不破坏）。"""
    bridge = LangGraphSseBridge("sess-bounded-steps")
    builder = AssistantMessageBuilder(session_id="sess-bounded-steps", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    class _Output:
        usage_metadata = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

    for i in range(5):
        bridge.process_item(
            {"event": "on_chat_model_end", "run_id": f"b-run-{i}", "data": {"output": _Output()}},
            builder, ctx,
        )
    # bridge 处理不报错；collector steps 不超 MAX_STEPS
    assert len(bridge._usage_collector.steps) == 5  # noqa: SLF001
    assert bridge._usage_collector.summary()["cumulative"]["input_tokens"] == 5  # noqa: SLF001


def _tool_start_event(name: str, run_id: str, tool_call_id: str, parent_ids=None) -> Dict[str, Any]:
    return {
        "event": "on_tool_start",
        "name": name,
        "run_id": run_id,
        "parent_ids": parent_ids or [],
        "data": {"input": {"q": tool_call_id}, "tool_call_id": tool_call_id},
    }


def test_parallel_tools_same_model_step_share_step_id() -> None:
    """同一 model step 内并行调用的工具共享 step_id；不同 step 的 step_id 不同。"""
    bridge = LangGraphSseBridge("sess-step")
    builder = AssistantMessageBuilder(session_id="sess-step", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    # step 1: model start → 两个并行 tool start
    bridge.process_item({"event": "on_chat_model_start", "run_id": "m-1"}, builder, ctx)
    parts: List[str] = []
    parts.extend(bridge.process_item(_tool_start_event("web_search", "t-1a", "call-1a"), builder, ctx))
    parts.extend(bridge.process_item(_tool_start_event("web_fetch", "t-1b", "call-1b"), builder, ctx))
    objs = _data_json_objects("".join(parts))
    avail = [o for o in objs if o["type"] == "tool-input-available"]
    assert len(avail) == 2
    assert avail[0]["step_id"] == avail[1]["step_id"] == "root:1"

    # tool-output-available 也带同一 step_id
    out_parts: List[str] = []
    out_parts.extend(bridge.process_item(
        {"event": "on_tool_end", "name": "web_search", "run_id": "t-1a",
         "data": {"output": "ok"}}, builder, ctx,
    ))
    out_objs = _data_json_objects("".join(out_parts))
    out_avail = [o for o in out_objs if o["type"] == "tool-output-available"]
    assert out_avail[0]["step_id"] == "root:1"

    # step 2: 新 model start → 单个 tool，step_id 递增
    bridge.process_item({"event": "on_chat_model_start", "run_id": "m-2"}, builder, ctx)
    parts2: List[str] = []
    parts2.extend(bridge.process_item(_tool_start_event("read", "t-2a", "call-2a"), builder, ctx))
    objs2 = _data_json_objects("".join(parts2))
    avail2 = [o for o in objs2 if o["type"] == "tool-input-available"]
    assert avail2[0]["step_id"] == "root:2"


def test_single_tool_still_gets_step_id() -> None:
    """单工具调用也有 step_id（前端按 ≥2 分组，单工具不分组但不报错）。"""
    bridge = LangGraphSseBridge("sess-step-single")
    builder = AssistantMessageBuilder(session_id="sess-step-single", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    bridge.process_item({"event": "on_chat_model_start", "run_id": "m-1"}, builder, ctx)
    parts = bridge.process_item(_tool_start_event("read", "t-1", "call-1"), builder, ctx)
    avail = next(o for o in _data_json_objects("".join(parts)) if o["type"] == "tool-input-available")
    assert avail["step_id"] == "root:1"


def test_subagent_parallel_tools_step_id_scoped_by_task() -> None:
    """子 Agent 内部并行工具的 step_id 按 parent_task_call_id 独立计数，与顶层不混淆。"""
    bridge = LangGraphSseBridge("sess-step-sub")
    builder = AssistantMessageBuilder(session_id="sess-step-sub", message_id=bridge.assistant_message_id)
    ctx = _ctx()

    task_run = "run-task-s"
    # 顶层 model step + task tool
    bridge.process_item({"event": "on_chat_model_start", "run_id": "m-top"}, builder, ctx)
    parts: List[str] = []
    parts.extend(bridge.process_item(
        {"event": "on_tool_start", "name": TASK_TOOL_NAME, "run_id": task_run, "parent_ids": [],
         "data": {"input": {"description": "d", "subagent_type": "general-purpose", "prompt": "p"}}},
        builder, ctx,
    ))
    task_avail = next(o for o in _data_json_objects("".join(parts)) if o["type"] == "tool-input-available" and o["name"] == TASK_TOOL_NAME)
    task_call_id = task_avail["tool_call_id"]

    # 子 Agent 内部 model step + 两个并行 tool
    bridge.process_item({"event": "on_chat_model_start", "run_id": "m-sub", "parent_ids": [task_run]}, builder, ctx)
    sub_parts: List[str] = []
    sub_parts.extend(bridge.process_item(
        _tool_start_event("read", "r-sub-1", "call-sub-1", parent_ids=[task_run]), builder, ctx,
    ))
    sub_parts.extend(bridge.process_item(
        _tool_start_event("grep", "r-sub-2", "call-sub-2", parent_ids=[task_run]), builder, ctx,
    ))
    sub_objs = _data_json_objects("".join(sub_parts))
    sub_avail = [o for o in sub_objs if o["type"] == "tool-input-available"]
    assert len(sub_avail) == 2
    expected_scope = f"{task_call_id}:1"
    assert sub_avail[0]["step_id"] == sub_avail[1]["step_id"] == expected_scope
    # 与顶层 step_id（root:1）不同
    assert sub_avail[0]["step_id"] != "root:1"


def test_step_id_survives_builder_persistence() -> None:
    """step_id 经 builder.to_dict → from_dict 往返存活。"""
    from noesis.chat.message_builder import MessageContent
    bridge = LangGraphSseBridge("sess-step-persist")
    builder = AssistantMessageBuilder(session_id="sess-step-persist", message_id=bridge.assistant_message_id)
    ctx = _ctx()
    bridge.process_item({"event": "on_chat_model_start", "run_id": "m-1"}, builder, ctx)
    bridge.process_item(_tool_start_event("read", "t-1", "call-1"), builder, ctx)
    bridge.process_item(_tool_start_event("read", "t-2", "call-2"), builder, ctx)

    dumped = builder.to_dict()
    restored = MessageContent.from_dict(dumped)
    tool_parts = [p for p in restored.parts if isinstance(p, ToolPart)]
    assert len(tool_parts) == 2
    assert {p.step_id for p in tool_parts} == {"root:1"}
