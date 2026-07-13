"""用户注册服务单测。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from exceptions.exception import ConflictException
from exceptions.exception import LoginException
from domain.auth.registration_invite import RegistrationInviteService
from schemas.login_vo import UserRegister, UserRegistrationRequest
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


@pytest.mark.asyncio
async def test_register_with_invite_verifies_code_and_creates_user() -> None:
    db = _mock_db_with_existing_user(existing=False)
    body = UserRegistrationRequest(username="newuser", password="secret1", invite_code="123456")
    with patch("services.login_service.RegistrationInviteService.verify", new=AsyncMock()) as verify:
        user = await LoginService.register_with_invite(db, body)

    assert user.username == "newuser"
    verify.assert_awaited_once_with(db, body.invite_code)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_with_invite_does_not_create_user_when_code_invalid() -> None:
    db = _mock_db_with_existing_user(existing=False)
    body = UserRegistrationRequest(username="newuser", password="secret1", invite_code="123456")
    with patch(
        "services.login_service.RegistrationInviteService.verify",
        new=AsyncMock(side_effect=LoginException(message="邀请码无效")),
    ):
        with pytest.raises(LoginException):
            await LoginService.register_with_invite(db, body)

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_invite_can_be_verified_repeatedly() -> None:
    owner = MagicMock(registration_invite_digest="8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92")
    result = MagicMock()
    result.scalar_one_or_none.return_value = owner
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    await RegistrationInviteService.verify(db, "123456")
    await RegistrationInviteService.verify(db, "123456")

    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_rotate_invite_updates_admin_record() -> None:
    admin = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = admin
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with patch.object(RegistrationInviteService, "_new_code", return_value="123456"):
        code = await RegistrationInviteService.rotate(db)

    assert code == "123456"
    assert len(admin.registration_invite_digest) == 64
    assert isinstance(admin.registration_invite_updated_at, int)
    db.commit.assert_awaited_once()
