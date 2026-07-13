"""服务端 Cookie Session 的创建、校验、续期和撤销。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import SessionConfig
from models.db_models import TUserSession


def _now_ms() -> int:
    return int(time.time() * 1000)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    session: TUserSession
    raw_session_id: str
    csrf_token: str


class SessionService:
    @staticmethod
    def _expiry(now: int) -> tuple[int, int]:
        idle = now + SessionConfig.idle_expire_days * 86_400_000
        absolute = now + SessionConfig.absolute_expire_days * 86_400_000
        return idle, absolute

    @classmethod
    async def create(
        cls, db: AsyncSession, user_id: int, user_agent: str = "", client_ip: str | None = None
    ) -> IssuedSession:
        now = _now_ms()
        raw_session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        idle, absolute = cls._expiry(now)
        session = TUserSession(
            id=str(uuid.uuid4()), user_id=user_id, session_digest=_digest(raw_session_id),
            csrf_digest=_digest(csrf_token), created_at=now, last_seen_at=now,
            idle_expires_at=idle, absolute_expires_at=absolute, revoked_at=None,
            device_name=(user_agent[:200] or None),
            user_agent_digest=(_digest(user_agent) if user_agent else None), last_ip=client_ip,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return IssuedSession(session, raw_session_id, csrf_token)

    @classmethod
    async def get_valid(cls, db: AsyncSession, raw_session_id: str | None) -> TUserSession | None:
        if not raw_session_id:
            return None
        result = await db.execute(select(TUserSession).where(TUserSession.session_digest == _digest(raw_session_id)))
        session = result.scalar_one_or_none()
        now = _now_ms()
        if session is None or session.revoked_at is not None or session.idle_expires_at <= now or session.absolute_expires_at <= now:
            return None
        return session

    @classmethod
    async def touch(cls, db: AsyncSession, session: TUserSession) -> TUserSession:
        now = _now_ms()
        window = SessionConfig.renewal_window_minutes * 60_000
        if now - session.last_seen_at < window:
            return session
        idle, _ = cls._expiry(now)
        session.last_seen_at = now
        session.idle_expires_at = min(idle, session.absolute_expires_at)
        await db.commit()
        await db.refresh(session)
        return session

    @staticmethod
    def remaining_seconds(session: TUserSession) -> int:
        """Cookie Max-Age 不得超过服务端空闲/绝对会话剩余时间。"""
        return max(0, (min(session.idle_expires_at, session.absolute_expires_at) - _now_ms()) // 1000)

    @classmethod
    def verify_csrf(cls, session: TUserSession, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(session.csrf_digest, _digest(token))

    @classmethod
    async def rotate_csrf(cls, db: AsyncSession, session: TUserSession) -> str:
        token = secrets.token_urlsafe(32)
        session.csrf_digest = _digest(token)
        await db.commit()
        await db.refresh(session)
        return token

    @classmethod
    async def revoke(cls, db: AsyncSession, session: TUserSession) -> None:
        if session.revoked_at is None:
            session.revoked_at = _now_ms()
            await db.commit()

    @classmethod
    async def revoke_all(cls, db: AsyncSession, user_id: int) -> None:
        await db.execute(update(TUserSession).where(TUserSession.user_id == user_id, TUserSession.revoked_at.is_(None)).values(revoked_at=_now_ms()))
        await db.commit()

    @classmethod
    async def list_active(cls, db: AsyncSession, user_id: int) -> list[TUserSession]:
        now = _now_ms()
        result = await db.execute(select(TUserSession).where(TUserSession.user_id == user_id, TUserSession.revoked_at.is_(None), TUserSession.idle_expires_at > now, TUserSession.absolute_expires_at > now).order_by(TUserSession.last_seen_at.desc()))
        return list(result.scalars())

    @classmethod
    async def revoke_by_id(cls, db: AsyncSession, user_id: int, session_id: str) -> bool:
        result = await db.execute(select(TUserSession).where(TUserSession.id == session_id, TUserSession.user_id == user_id))
        session = result.scalar_one_or_none()
        if session is None:
            return False
        await cls.revoke(db, session)
        return True
