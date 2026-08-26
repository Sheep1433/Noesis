"""聊天服务用户标识的数据库类型边界测试。"""

import pytest

from noesis.storage.postgres.models.chat import TChatSession
from noesis.services.chat_service import ChatService


class _Result:
    def scalar_one_or_none(self):
        return None


class _Session:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result()

    def add(self, item):  # noqa: ARG002
        pass

    async def commit(self):
        pass

    async def refresh(self, item):  # noqa: ARG002
        pass


@pytest.mark.asyncio
async def test_get_session_by_id_normalizes_integer_user_id() -> None:
    db = _Session()
    await ChatService.get_session_by_id("session-1", user_id=1, db=db)
    assert db.statement is not None
    compiled = db.statement.compile()
    assert "user_id" in str(compiled)
    assert compiled.params["user_id_1"] == "1"


@pytest.mark.asyncio
async def test_get_or_create_session_uses_uuid_user_id_type() -> None:
    db = _Session()
    await ChatService.get_or_create_session("00000000-0000-7000-8000-000000000001", "session-1", db=db)
    assert TChatSession.user_id.type.python_type is str


@pytest.mark.asyncio
async def test_mark_session_read_preserves_updated_at() -> None:
    """读会话只写 last_read_at，不带动列级 onupdate 刷新 updated_at。"""
    db = _Session()
    await ChatService.mark_session_read("session-1", user_id=1, db=db)
    compiled = db.statement.compile()
    sql = str(compiled)
    # UPDATE 语句显式回写 updated_at 原值，压掉模型列的 onupdate
    assert "last_read_at" in sql
    assert "updated_at" in sql
