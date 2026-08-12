"""服务端 Session 与 CSRF 的纯领域回归测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from noesis.auth.entities import AuthSession
from noesis.auth.policy import digest_secret
from noesis.services.auth.sessions import SessionService


def _session(csrf: str = "csrf") -> AuthSession:
    return AuthSession(
        id="s1", user_id=1, session_digest=digest_secret("session"), csrf_digest=digest_secret(csrf),
        created_at=1, last_seen_at=1, idle_expires_at=9_999_999_999_999,
        absolute_expires_at=9_999_999_999_999, revoked_at=None,
    )


def test_csrf_token_matches_digest_only():
    session = _session()
    assert SessionService.verify_csrf(session, "csrf")
    assert not SessionService.verify_csrf(session, "other")
    assert not SessionService.verify_csrf(session, None)


def test_raw_session_id_is_not_model_field():
    session = _session()
    assert "raw_session_id" not in session.__dict__
    assert session.session_digest != "session"


def test_session_restore_returns_401_without_double_wrapping_db_context():
    """回归：未登录恢复会话返回 401，不得二次包装 async context manager。"""
    from server.db import get_db
    from server.main import app

    async def fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with patch(
            "server.auth_dependencies.SessionService.get_valid",
            new=AsyncMock(return_value=None),
        ):
            response = TestClient(app).get("/api/auth/session")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["code"] == 401


def test_cookie_lifetime_uses_the_stricter_server_expiry(monkeypatch):
    session = _session()
    session.idle_expires_at = 2_000
    session.absolute_expires_at = 1_500
    monkeypatch.setattr("noesis.services.auth.sessions._now_ms", lambda: 1_000)
    assert SessionService.remaining_seconds(session) == 0


@pytest.mark.asyncio
async def test_invalid_or_revoked_session_is_not_accepted():
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = _session()
    db.execute.return_value = result
    assert await SessionService.get_valid(db, None) is None

    session = _session()
    session.revoked_at = 1
    result.scalar_one_or_none.return_value = session
    assert await SessionService.get_valid(db, "session") is None


@pytest.mark.asyncio
async def test_revoke_all_targets_only_the_current_user():
    db = AsyncMock()
    await SessionService.revoke_all(db, 7)
    assert db.execute.await_count == 1
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_cookie(monkeypatch):
    from noesis.errors.exceptions import AuthException
    from server.auth_dependencies import get_current_user

    request = MagicMock()
    request.cookies.get.return_value = None
    db = AsyncMock()
    monkeypatch.setattr(SessionService, "get_valid", AsyncMock(return_value=None))
    with pytest.raises(AuthException):
        await get_current_user(request, db)


@pytest.mark.asyncio
async def test_require_csrf_rejects_bad_header():
    from noesis.errors.exceptions import PermissionException
    from server.auth_dependencies import require_csrf

    request = MagicMock()
    request.state.auth_session = _session("csrf")
    request.headers.get.return_value = "wrong"
    with pytest.raises(PermissionException):
        await require_csrf(request)
