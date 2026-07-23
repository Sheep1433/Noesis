"""流式 assistant 终态落库幂等：正常 finalize 与断连收尾不得重复写入。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge
from services.qa_service import (
    QaService,
    _ActiveStreamState,
    _finalize_streaming_assistant,
    _handle_stream_client_disconnect,
    _mark_stream_persist_finalized,
    _resolve_assistant_message_id,
)


def test_resolve_assistant_message_id_prefers_ctx_then_builder() -> None:
    builder = AssistantMessageBuilder(session_id="s1", message_id="mid-bridge")
    assert _resolve_assistant_message_id({"_assistant_db_id": "mid-ctx"}, builder) == "mid-ctx"
    assert _resolve_assistant_message_id({}, builder) == "mid-bridge"
    assert _resolve_assistant_message_id({}, None) is None


@pytest.mark.asyncio
async def test_finalize_then_disconnect_persists_once() -> None:
    session_id = "sess-idem-1"
    builder = AssistantMessageBuilder(session_id=session_id, message_id="msg-1")
    builder.append_text("回答正文")
    bridge = LangGraphSseBridge(session_id)
    bridge.last_finish_reason = "stop"
    ctx = {"_assistant_db_id": "msg-1", "text_buffer": ""}

    mock_persist = AsyncMock()
    user = SimpleNamespace(user_id="u1")

    with patch("services.qa.helpers._persist_assistant", mock_persist):
        await _finalize_streaming_assistant(
            builder=builder,
            bridge=bridge,
            ctx=ctx,
            session_id=session_id,
            user_id=user.user_id,
            qa_type="COMMON_QA",
        )
        await _handle_stream_client_disconnect(
            session_id=session_id,
            qa_type="COMMON_QA",
            user_id=user.user_id,
            ctx=ctx,
            builder=builder,
            log_label="test",
        )

    mock_persist.assert_awaited_once()
    assert mock_persist.await_args.kwargs["status"] == "completed"
    assert mock_persist.await_args.kwargs["assistant_message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_disconnect_then_finalize_persists_once() -> None:
    session_id = "sess-idem-2"
    builder = AssistantMessageBuilder(session_id=session_id, message_id="msg-2")
    builder.append_text("partial")
    ctx = {"_assistant_db_id": "msg-2", "text_buffer": ""}
    mock_persist = AsyncMock()
    user = SimpleNamespace(user_id="u1")

    QaService._active_streams[session_id] = _ActiveStreamState(
        builder=builder,
        ctx=ctx,
        qa_type="COMMON_QA",
    )
    bridge = LangGraphSseBridge(session_id)
    bridge.last_finish_reason = "stop"

    try:
        with patch("services.qa.helpers._persist_assistant", mock_persist):
            await _handle_stream_client_disconnect(
                session_id=session_id,
                qa_type="COMMON_QA",
                user_id=user.user_id,
                ctx=ctx,
                builder=builder,
                log_label="test",
            )
            await _finalize_streaming_assistant(
                builder=builder,
                bridge=bridge,
                ctx=ctx,
                session_id=session_id,
                user_id=user.user_id,
                qa_type="COMMON_QA",
            )

        mock_persist.assert_awaited_once()
        assert mock_persist.await_args.kwargs["status"] == "partial"
    finally:
        QaService._active_streams.pop(session_id, None)


@pytest.mark.asyncio
async def test_finalize_uses_builder_message_id_when_ctx_missing() -> None:
    """骨架 ctx 丢失时仍应 UPDATE 同一 assistant_message_id，而非 INSERT 新行。"""
    session_id = "sess-idem-3"
    builder = AssistantMessageBuilder(session_id=session_id, message_id="msg-3")
    builder.append_text("正文")
    bridge = LangGraphSseBridge(session_id)
    ctx = {"text_buffer": ""}
    mock_persist = AsyncMock()

    with patch("services.qa.helpers._persist_assistant", mock_persist):
        await _finalize_streaming_assistant(
            builder=builder,
            bridge=bridge,
            ctx=ctx,
            session_id=session_id,
            user_id="u1",
            qa_type="COMMON_QA",
        )

    mock_persist.assert_awaited_once()
    assert mock_persist.await_args.kwargs["assistant_message_id"] == "msg-3"


@pytest.mark.asyncio
async def test_double_disconnect_handler_is_idempotent() -> None:
    session_id = "sess-idem-4"
    builder = AssistantMessageBuilder(session_id=session_id, message_id="msg-4")
    builder.append_text("x")
    ctx = {"_assistant_db_id": "msg-4", "text_buffer": ""}
    mock_persist = AsyncMock()

    with patch("services.qa.helpers._persist_assistant", mock_persist):
        await _handle_stream_client_disconnect(
            session_id=session_id,
            qa_type="COMMON_QA",
            user_id="u1",
            ctx=ctx,
            builder=builder,
            log_label="test",
        )
        await _handle_stream_client_disconnect(
            session_id=session_id,
            qa_type="COMMON_QA",
            user_id="u1",
            ctx=ctx,
            builder=builder,
            log_label="test",
        )

    mock_persist.assert_awaited_once()


def test_mark_stream_persist_finalized_blocks_disconnect() -> None:
    ctx: dict = {"_assistant_db_id": "m1"}
    _mark_stream_persist_finalized(ctx)
    assert ctx["_stream_persist_finalized"] is True
