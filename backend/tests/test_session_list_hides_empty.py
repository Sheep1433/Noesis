"""会话列表 SHALL 过滤无 user 消息的空壳（chat-surface-lifecycle）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.chat_service import ChatService


@pytest.mark.asyncio
async def test_query_user_sessions_for_record_excludes_sessions_without_user_message() -> None:
    """exists(user message) 条件须进入 WHERE，避免 ensure 空壳进侧栏。"""
    db = AsyncMock()
    cnt_result = MagicMock()
    cnt_result.scalar_one.return_value = 0
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(side_effect=[cnt_result, list_result])

    sessions, total = await ChatService.query_user_sessions_for_record(
        user_id="u1",
        db=db,
        page=1,
        limit=10,
    )

    assert sessions == []
    assert total == 0
    assert db.execute.await_count == 2
    count_stmt = db.execute.await_args_list[0].args[0]
    sql = str(count_stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "t_chat_message" in sql.lower() or "TChatMessage" in sql or "EXISTS" in sql.upper()


@pytest.mark.asyncio
async def test_get_user_sessions_applies_user_message_exists_filter() -> None:
    db = AsyncMock()
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=list_result)

    out = await ChatService.get_user_sessions(user_id="u1", db=db)
    assert out == []
    stmt = db.execute.await_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    # 关联 user 消息过滤（exists 子查询）
    compiled = sql.upper()
    assert "EXISTS" in compiled or "T_CHAT_MESSAGE" in compiled
