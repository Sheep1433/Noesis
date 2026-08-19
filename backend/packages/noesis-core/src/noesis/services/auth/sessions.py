"""Session application service and transaction boundaries."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import SessionConfig
from noesis.auth.entities import AuthSession
from noesis.auth.ports import SessionRepository
from noesis.auth.policy import (
    digest_secret,
    is_session_valid,
    remaining_seconds,
    session_expiry,
    touch_session,
    verify_csrf,
)
from noesis.repositories.auth_repository import SqlAlchemySessionRepository


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class IssuedSession:
    session: AuthSession
    raw_session_id: str
    csrf_token: str


class SessionService:
    @staticmethod
    def _repository(db: AsyncSession) -> SessionRepository:
        return SqlAlchemySessionRepository(db)

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        user_id: int,
        user_agent: str = "",
        client_ip: str | None = None,
    ) -> IssuedSession:
        now = _now_ms()
        raw_session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        idle, absolute = session_expiry(
            now,
            idle_days=SessionConfig.idle_expire_days,
            absolute_days=SessionConfig.absolute_expire_days,
        )
        session = AuthSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            session_digest=digest_secret(raw_session_id),
            csrf_digest=digest_secret(csrf_token),
            created_at=now,
            last_seen_at=now,
            idle_expires_at=idle,
            absolute_expires_at=absolute,
            device_name=user_agent[:200] or None,
            user_agent_digest=digest_secret(user_agent) if user_agent else None,
            last_ip=client_ip,
        )
        await cls._repository(db).add(session)
        await db.commit()
        return IssuedSession(session, raw_session_id, csrf_token)

    @classmethod
    async def get_valid(cls, db: AsyncSession, raw_session_id: str | None) -> AuthSession | None:
        if not raw_session_id:
            return None
        session = await cls._repository(db).get_by_digest(
            digest_secret(raw_session_id)
        )
        if session is None or not is_session_valid(session, _now_ms()):
            return None
        return session

    @classmethod
    async def touch(cls, db: AsyncSession, session: AuthSession) -> AuthSession:
        changed = touch_session(
            session,
            now_ms=_now_ms(),
            renewal_window_minutes=SessionConfig.renewal_window_minutes,
            idle_days=SessionConfig.idle_expire_days,
        )
        if changed:
            await cls._repository(db).save(session)
            await db.commit()
        return session

    @staticmethod
    def remaining_seconds(session: AuthSession) -> int:
        return remaining_seconds(session, _now_ms())

    @staticmethod
    def verify_csrf(session: AuthSession, token: str | None) -> bool:
        return verify_csrf(session, token)

    @classmethod
    async def rotate_csrf(cls, db: AsyncSession, session: AuthSession) -> str:
        token = secrets.token_urlsafe(32)
        # 旧摘要保留一代：其它已加载窗口（跨标签/跨浏览器）持有的旧 token
        # 在本次轮换后仍有效，避免新窗口打开导致旧窗口全部 403。
        session.prev_csrf_digest = session.csrf_digest
        session.csrf_digest = digest_secret(token)
        await cls._repository(db).save(session)
        await db.commit()
        return token

    @classmethod
    async def revoke(cls, db: AsyncSession, session: AuthSession) -> None:
        if session.revoked_at is None:
            session.revoked_at = _now_ms()
            await cls._repository(db).save(session)
            await db.commit()

    @classmethod
    async def revoke_all(cls, db: AsyncSession, user_id: int) -> None:
        await cls._repository(db).revoke_all(user_id, _now_ms())
        await db.commit()

    @classmethod
    async def list_active(cls, db: AsyncSession, user_id: int) -> list[AuthSession]:
        return await cls._repository(db).list_active(user_id, _now_ms())

    @classmethod
    async def revoke_by_id(cls, db: AsyncSession, user_id: int, session_id: str) -> bool:
        repository = cls._repository(db)
        session = await repository.get_by_id_for_user(session_id, user_id)
        if session is None:
            return False
        session.revoked_at = _now_ms()
        await repository.save(session)
        await db.commit()
        return True
