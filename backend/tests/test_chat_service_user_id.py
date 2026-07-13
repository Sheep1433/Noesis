"""聊天服务用户标识的数据库类型边界测试。"""

import pytest

from models.chat_models import TChatSession
from services.chat_service import ChatService


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
async def test_get_or_create_session_normalizes_integer_user_id() -> None:
    db = _Session()
    await ChatService.get_or_create_session(1, "session-1", db=db)
    assert TChatSession.user_id.type.process_bind_param(1, None) == "1"
