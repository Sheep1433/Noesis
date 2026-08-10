"""会话标题：同一会话仅首条用户消息可设定，后续不覆盖。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from noesis.storage.postgres.models.chat import TChatSession
from noesis.services.chat_service import ChatService


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
        "noesis.services.chat_service.ChatService.update_session_title",
        new_callable=AsyncMock,
    ) as upd:
        from noesis.services.chat_service import ChatService

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
        "noesis.services.chat_service.ChatService.update_session_title",
        new_callable=AsyncMock,
    ) as upd:
        from noesis.services.chat_service import ChatService

        out = await ChatService.set_session_title_if_default(
            session_id="session-1",
            user_id="user-1",
            title="第二条问题",
            db=db,
        )

    upd.assert_not_awaited()
    assert out is existing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  第一行\n第二行  ", "第一行 第二行"),
        ("", "新对话"),
        ("   \n  ", "新对话"),
        (None, "新对话"),
    ],
)
def test_apply_default_session_title_normalizes_visible_text(raw, expected) -> None:
    session = _session("新对话")
    assert ChatService.apply_default_session_title(session, raw) == expected
    assert session.title == expected


def test_apply_default_session_title_is_idempotent_and_preserves_custom_title() -> None:
    session = _session("已有标题")
    assert ChatService.apply_default_session_title(session, "新的问题") == "已有标题"
    assert ChatService.apply_default_session_title(session, "再次发送") == "已有标题"
