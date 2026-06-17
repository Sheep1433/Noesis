"""用户注册服务单测。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from exceptions.exception import ConflictException
from schemas.login_vo import UserRegister
from services.login_service import LoginService


def _mock_db_with_existing_user(existing: bool) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = MagicMock() if existing else None
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_register_user_success() -> None:
    db = _mock_db_with_existing_user(existing=False)
    body = UserRegister(username='newuser', password='secret1')
    user = await LoginService.register_user(db, body)
    assert user.username == 'newuser'
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_user_conflict() -> None:
    db = _mock_db_with_existing_user(existing=True)
    body = UserRegister(username='admin', password='secret1')
    with pytest.raises(ConflictException) as exc_info:
        await LoginService.register_user(db, body)
    assert exc_info.value.message == '用户名已存在'
