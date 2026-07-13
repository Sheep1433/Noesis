"""登录路径不得在 Session 提交后访问已过期的 Async ORM 用户实体。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from api.auth_api import login, register
from schemas.login_vo import UserLogin, UserRegistrationRequest


class _ExpiredAfterSessionCreateUser:
    def __init__(self) -> None:
        self._expired = False
        self._id = 7
        self._username = "alice"
        self._mobile = "13800000000"

    def _get(self, value):
        if self._expired:
            raise AssertionError("提交 Session 后不应再访问 ORM user 属性")
        return value

    @property
    def id(self):
        return self._get(self._id)

    @property
    def username(self):
        return self._get(self._username)

    @property
    def mobile(self):
        return self._get(self._mobile)


@pytest.mark.asyncio
async def test_login_reads_user_fields_before_session_commit():
    user = _ExpiredAfterSessionCreateUser()
    request = Request({"type": "http", "method": "POST", "path": "/api/auth/login", "headers": [], "client": ("127.0.0.1", 1234)})

    async def create_session(*_args, **_kwargs):
        user._expired = True
        return SimpleNamespace(
            session=SimpleNamespace(id="session-1", created_at=1, last_seen_at=1, idle_expires_at=2, absolute_expires_at=3),
            raw_session_id="raw-session",
            csrf_token="csrf-token",
        )

    with patch("api.auth_api.LoginService.authenticate_user", new=AsyncMock(return_value=user)), patch(
        "api.auth_api.SessionService.create", side_effect=create_session,
    ):
        response = await login(request, SimpleNamespace(username="alice", password="secret"), AsyncMock())

    assert response.status_code == 200
    assert b'"username":"alice"' in response.body


@pytest.mark.asyncio
async def test_register_issues_session_cookie():
    user = SimpleNamespace(id=8, username="newuser", mobile=None)
    request = Request({"type": "http", "method": "POST", "path": "/api/auth/register", "headers": [], "client": ("127.0.0.1", 1234)})
    issued = SimpleNamespace(
        session=SimpleNamespace(id="session-1", created_at=1, last_seen_at=1, idle_expires_at=2, absolute_expires_at=3),
        raw_session_id="raw-session",
        csrf_token="csrf-token",
    )
    body = UserRegistrationRequest(username="newuser", password="secret1", invite_code="123456")

    with patch("api.auth_api.LoginService.register_with_invite", new=AsyncMock(return_value=user)), patch(
        "api.auth_api.SessionService.create", new=AsyncMock(return_value=issued)
    ):
        response = await register(request, body, AsyncMock())

    assert response.status_code == 200
    assert b'"username":"newuser"' in response.body
    assert "noesis_session=raw-session" in response.headers["set-cookie"]
