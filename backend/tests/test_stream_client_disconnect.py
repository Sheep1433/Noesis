"""客户端断开时 assistant partial 落库。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from domain.chat.message_builder import AssistantMessageBuilder
from services.qa_service import QaService, _ActiveStreamState, _handle_stream_client_disconnect


@pytest.mark.asyncio
async def test_handle_stream_client_disconnect_flushes_text_buffer() -> None:
    session_id = "sess-disc-1"
    builder = AssistantMessageBuilder(session_id=session_id, message_id="msg-1")
    ctx = {"text_buffer": "未刷新的正文", "_assistant_db_id": "msg-1"}

    QaService._active_streams[session_id] = _ActiveStreamState(
        builder=builder,
        ctx=ctx,
        qa_type="COMMON_QA",
    )

    mock_persist = AsyncMock()
    user = SimpleNamespace(user_id="u1")

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

        mock_persist.assert_awaited_once()
        content_dict = mock_persist.await_args.args[0]
        text_parts = [p for p in content_dict["parts"] if p.get("type") == "text"]
        assert any("未刷新的正文" in str(p.get("content", "")) for p in text_parts)
        assert mock_persist.await_args.kwargs["status"] == "partial"
    finally:
        QaService._active_streams.pop(session_id, None)
