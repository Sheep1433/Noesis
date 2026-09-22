"""流式过程中不得按 token 落库或拆成多个 text part。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge
from noesis.chat.event_mapping.bridge import END_SENTINEL
from noesis.chat.event_mapping.mapper import new_stream_ctx
from noesis.services.qa.helpers import (
    _flush_ctx_text_buffer,
    _persist_stream_checkpoint,
    _yield_run_events_from_agent,
)



from noesis.chat.delivery.sse import encode_run_event as _encode_run_event
from noesis.chat.delivery.sse import format_sse


def _encode_frames(event):
    """typed 事件 → 完整 SSE 帧字符串列表（与生产 bytes 帧同形，文本形态便于断言）。"""
    from noesis.chat.delivery.sse import encode_run_event, format_sse, format_done
    if type(event).__name__ == "StreamDone":
        return [format_done()]
    return [format_sse(name, payload) for name, payload in encode_run_event(event)]

def _raw_sse_lines(bridge, item, builder, ctx):
    """raw 运行时事件 → SSE 行（与生产主路径同形：map_item 后经统一编码器，bytes 帧转文本行）。"""
    return [
        frame
        for event in bridge.map_item(item, builder, ctx)
        for frame in _encode_frames(event)
    ]


def _finalize_sse_lines(bridge, finish_reason=None):
    """bridge 终态 → SSE 行（与生产主路径同形：finalize_events 后经统一编码器）。"""
    return [
        frame
        for event in bridge.finalize_events(finish_reason=finish_reason)
        for frame in _encode_frames(event)
    ]


@pytest.mark.asyncio
async def test_persist_stream_checkpoint_does_not_flush_text_buffer() -> None:
    bridge = LangGraphSseBridge("sess-mid")
    builder = AssistantMessageBuilder(session_id="sess-mid", message_id=bridge.assistant_message_id)
    ctx = {
        "text_buffer": "累积正文",
        "text_buffer_parent_task_call_id": None,
        "_assistant_db_id": "mid-1",
    }
    bridge._persist_tick = True

    with patch("noesis.services.qa.helpers._persist_assistant", new_callable=AsyncMock) as mock_persist:
        await _persist_stream_checkpoint(bridge, "sess-mid", "u1")

    mock_persist.assert_not_awaited()
    assert ctx["text_buffer"] == "累积正文"
    assert builder.is_empty()


@pytest.mark.asyncio
async def test_run_event_path_persists_context_after_model_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务模式收到 model_end 后，应把 context 快照写入 session extra。"""
    bridge = LangGraphSseBridge("sess-run")
    builder = AssistantMessageBuilder(session_id="sess-run", message_id=bridge.assistant_message_id)

    async def fake_iter_bridge_events(*args, **kwargs):
        yield {
            "event": "on_chat_model_end",
            "run_id": "model-run-1",
            "data": {
                "output": AIMessage(
                    content="完成",
                    usage_metadata={
                        "input_tokens": 1234,
                        "output_tokens": 20,
                        "total_tokens": 1254,
                    },
                ),
            },
        }
        yield END_SENTINEL

    persist = AsyncMock()
    monkeypatch.setattr(
        "noesis.services.qa.helpers.iter_bridge_events",
        fake_iter_bridge_events,
    )
    monkeypatch.setattr(
        "noesis.services.qa.helpers._persist_stream_checkpoint",
        persist,
    )

    async def empty_agent():
        if False:
            yield None

    events = [
        event
        async for event in _yield_run_events_from_agent(
            empty_agent(),
            bridge=bridge,
            builder=builder,
            ctx=new_stream_ctx(),
            session_id="sess-run",
            user_id="u1",
            qa_type="SUPER_AGENT_QA",
        )
    ]

    assert events
    persist.assert_awaited_once_with(bridge, "sess-run", "u1")


def test_flush_ctx_text_buffer_merges_single_text_part() -> None:
    builder = AssistantMessageBuilder(session_id="s", message_id="m")
    ctx = {"text_buffer": "你好", "text_buffer_parent_task_call_id": None}
    _flush_ctx_text_buffer(ctx, builder)
    ctx["text_buffer"] = "！"
    _flush_ctx_text_buffer(ctx, builder)

    text_parts = [p for p in builder.to_dict()["parts"] if p.get("type") == "text"]
    assert len(text_parts) == 1
    assert text_parts[0]["content"] == "你好！"


def test_streaming_finish_yields_one_text_part() -> None:
    """模拟无工具的单轮流式：finish 后 builder 仅一个 text part。"""
    bridge = LangGraphSseBridge("sess-one-text")
    builder = AssistantMessageBuilder(
        session_id="sess-one-text",
        message_id=bridge.assistant_message_id,
    )
    ctx = {
        "text_buffer": "",
        "text_buffer_parent_task_call_id": None,
        "usage_cumulative": {"input_tokens": 0, "output_tokens": 0},
        "usage_seen_run_ids": set(),
    }

    class _Chunk:
        content = "你"
        additional_kwargs = {}

    class _Chunk2:
        content = "好"
        additional_kwargs = {}

    _raw_sse_lines(bridge, 
        {"event": "on_chat_model_stream", "run_id": "r1", "data": {"chunk": _Chunk()}},
        builder,
        ctx,
    )
    _raw_sse_lines(bridge, 
        {"event": "on_chat_model_stream", "run_id": "r1", "data": {"chunk": _Chunk2()}},
        builder,
        ctx,
    )
    _raw_sse_lines(bridge, {"type": "__tw_finish__", "usage": {}}, builder, ctx)
    _finalize_sse_lines(bridge)

    text_parts = [p for p in builder.to_dict()["parts"] if p.get("type") == "text"]
    assert len(text_parts) == 1
    assert text_parts[0]["content"] == "你好"
