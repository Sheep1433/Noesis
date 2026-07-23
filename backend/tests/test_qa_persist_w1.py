"""W1: _persist_assistant 将 status/extra 传入 save_message（无真实 DB）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_persist_assistant_passes_status_and_extra() -> None:
    db = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("services.qa.helpers.AsyncSessionLocal", return_value=cm),
        patch("services.qa.helpers.ChatService.save_message", new_callable=AsyncMock) as save_msg,
    ):
        from services.qa_service import _persist_assistant

        await _persist_assistant(
            {"version": 1, "parts": [{"type": "text", "id": "p1", "content": "x"}]},
            "session-1",
            "user-1",
            status="error",
            extra={"error_message": "boom"},
        )

    save_msg.assert_awaited_once()
    kwargs = save_msg.await_args.kwargs
    assert kwargs["status"] == "error"
    assert kwargs["extra"]["error_message"] == "boom"
    assert kwargs["role"] == "assistant"


@pytest.mark.asyncio
async def test_persist_assistant_update_path_calls_update() -> None:
    db = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("services.qa.helpers.AsyncSessionLocal", return_value=cm),
        patch("services.qa.helpers.ChatService.update_assistant_message", new_callable=AsyncMock) as upd,
        patch("services.qa.helpers.ChatService.save_message", new_callable=AsyncMock) as ins,
    ):
        from services.qa_service import _persist_assistant

        upd.return_value = True
        await _persist_assistant(
            {"version": 1, "parts": [{"type": "text", "id": "p1", "content": "hi"}]},
            "session-1",
            "user-1",
            status="streaming",
            assistant_message_id="mid-1",
        )

    upd.assert_awaited_once()
    ins.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_assistant_skips_empty_parts_without_row_id() -> None:
    with (
        patch("services.qa.helpers.AsyncSessionLocal") as mock_local,
        patch("services.qa.helpers.ChatService.save_message", new_callable=AsyncMock) as save_msg,
    ):
        from services.qa_service import _persist_assistant

        await _persist_assistant({"parts": []}, "s", "u", status="partial")

    save_msg.assert_not_awaited()
    mock_local.assert_not_called()
