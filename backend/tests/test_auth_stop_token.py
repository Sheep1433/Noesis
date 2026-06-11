"""鉴权滑动续期、stop_token 与 stop 接口双轨验签。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import Request

from config.env import JwtConfig
from schemas.qa_vo import QaStopRequest
from services.user_service import UserService
from utils.auth_token_service import AUTH_COOKIE_NAME, AuthTokenService
from utils.stop_token_service import StopTokenService


def _make_request(
    *,
    bearer: str | None = None,
    cookie: str | None = None,
    stop_header: str | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat/sessions/s1/stop",
        "headers": [],
    }
    if stop_header:
        scope["headers"].append((b"X-Stop-Token".lower(), stop_header.encode()))
    request = Request(scope)
    if bearer is not None:
        request.headers.__dict__["_list"] = [
            *(getattr(request.headers, "raw", []) or []),
            (b"authorization", f"Bearer {bearer}".encode()),
        ]
    if cookie is not None:
        request._cookies = {AUTH_COOKIE_NAME: cookie}  # type: ignore[attr-defined]
    return request


def test_stop_token_create_and_verify() -> None:
    token = StopTokenService.create("sess-a", 42)
    assert StopTokenService.verify("sess-a", token) == 42
    assert StopTokenService.verify("sess-b", token) is None


def test_stop_token_expired() -> None:
    expire = datetime.now(timezone.utc) - timedelta(minutes=1)
    payload = {
        "typ": "stop",
        "sid": "sess-x",
        "uid": "1",
        "exp": expire,
    }
    token = jwt.encode(payload, JwtConfig.jwt_secret_key, algorithm=JwtConfig.jwt_algorithm)
    assert StopTokenService.verify("sess-x", token) is None


def test_auth_token_service_issue_access_token() -> None:
    token = AuthTokenService.issue_access_token(1, "admin")
    payload = AuthTokenService.decode_access_token(token)
    assert payload.get("id") == "1"
    assert payload.get("name") == "admin"


@pytest.mark.asyncio
async def test_get_user_for_stop_accepts_stop_token() -> None:
    session_id = "sess-stop-auth"
    stop_token = StopTokenService.create(session_id, 7)
    stop_payload = QaStopRequest(session_id=session_id, qa_type="COMMON_QA", stop_token=stop_token)
    http_request = MagicMock(spec=Request)
    http_request.cookies = {}
    http_request.headers = {}
    http_request.state = MagicMock()

    mock_user = MagicMock()
    mock_user.id = 7
    mock_user.username = "u7"
    mock_user.mobile = None

    db = AsyncMock()
    scalar = MagicMock()
    scalar.scalar_one_or_none.return_value = mock_user
    db.execute = AsyncMock(return_value=scalar)

    user = await UserService.get_user_for_stop(
        session_id=session_id,
        http_request=http_request,
        stop_payload=stop_payload,
        token=None,
        db=db,
    )
    assert user.user_id == 7
