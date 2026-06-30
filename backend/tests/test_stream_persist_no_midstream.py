"""流式过程中不得按 token 落库或拆成多个 text part。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge
from services.qa_service import _flush_ctx_text_buffer, _persist_stream_checkpoint


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

    with patch("services.qa_service._persist_assistant", new_callable=AsyncMock) as mock_persist:
        await _persist_stream_checkpoint(
            bridge, ctx, builder, "sess-mid", "u1",
        )

    mock_persist.assert_not_awaited()
    assert ctx["text_buffer"] == "累积正文"
    assert builder.is_empty()


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

    bridge.process_item(
        {"event": "on_chat_model_stream", "run_id": "r1", "data": {"chunk": _Chunk()}},
        builder,
        ctx,
    )
    bridge.process_item(
        {"event": "on_chat_model_stream", "run_id": "r1", "data": {"chunk": _Chunk2()}},
        builder,
        ctx,
    )
    bridge.process_item({"type": "__tw_finish__", "usage": {}}, builder, ctx)
    bridge.finalize()

    text_parts = [p for p in builder.to_dict()["parts"] if p.get("type") == "text"]
    assert len(text_parts) == 1
    assert text_parts[0]["content"] == "你好"
