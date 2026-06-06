"""会话标题：同一会话仅首条用户消息可设定，后续不覆盖。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from model.chat_models import TChatSession


def _session(title: str) -> TChatSession:
    obj = TChatSession()
    obj.id = "session-1"
    obj.user_id = "user-1"
    obj.title = title
    return obj


@pytest.mark.asyncio
async def test_set_session_title_if_default_updates_new_dialog() -> None:
    db = AsyncMock()
    existing = _session("新对话")
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=result_mock)

    with patch(
        "services.chat_service.ChatService.update_session_title",
        new_callable=AsyncMock,
    ) as upd:
        from services.chat_service import ChatService

        await ChatService.set_session_title_if_default(
            session_id="session-1",
            user_id="user-1",
            title="第一条问题",
            db=db,
        )

    upd.assert_awaited_once_with(
        session_id="session-1",
        user_id="user-1",
        title="第一条问题",
        db=db,
    )


@pytest.mark.asyncio
async def test_set_session_title_if_default_skips_custom_title() -> None:
    db = AsyncMock()
    existing = _session("已有标题")
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=result_mock)

    with patch(
        "services.chat_service.ChatService.update_session_title",
        new_callable=AsyncMock,
    ) as upd:
        from services.chat_service import ChatService

        out = await ChatService.set_session_title_if_default(
            session_id="session-1",
            user_id="user-1",
            title="第二条问题",
            db=db,
        )

    upd.assert_not_awaited()
    assert out is existing
