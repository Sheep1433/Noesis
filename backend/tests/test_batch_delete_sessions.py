"""批量软删会话：跳过无效 ID，一次事务落库。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.chat_models import TChatSession


def _session(user_id: str, session_id: str) -> TChatSession:
    obj = TChatSession()
    obj.id = session_id
    obj.user_id = user_id
    obj.title = "测试"
    return obj


@pytest.mark.asyncio
async def test_batch_delete_sessions_skips_missing_ids() -> None:
    from services.chat_service import ChatService

    user_id = "1"
    keep_id = "sess-keep"
    missing_id = "sess-gone"

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [_session(user_id, keep_id)]
    db.execute = AsyncMock(return_value=result_mock)
    db.commit = AsyncMock()

    with patch("services.chat_service.cancel_session_agent_runs", new_callable=AsyncMock), patch(
        "services.chat_service.delete_session_workspace"
    ):
        deleted = await ChatService.batch_delete_sessions(
            [keep_id, missing_id, keep_id],
            user_id,
            db=db,
        )

    assert deleted == 1
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_delete_sessions_raises_when_none_found() -> None:
    from exceptions.exception import ServiceException
    from services.chat_service import ChatService

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)

    with pytest.raises(ServiceException) as exc_info:
        await ChatService.batch_delete_sessions(["missing"], "1", db=db)
    assert exc_info.value.message == "会话不存在"
