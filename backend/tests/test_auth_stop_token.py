"""服务端 Session 与 CSRF 的纯领域回归测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models.db_models import TUserSession
from domain.auth.session import SessionService


def _session(csrf: str = "csrf") -> TUserSession:
    from domain.auth.session import _digest
    return TUserSession(
        id="s1", user_id=1, session_digest=_digest("session"), csrf_digest=_digest(csrf),
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


def test_cookie_lifetime_uses_the_stricter_server_expiry(monkeypatch):
    session = _session()
    session.idle_expires_at = 2_000
    session.absolute_expires_at = 1_500
    monkeypatch.setattr("domain.auth.session._now_ms", lambda: 1_000)
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
