"""SQLAlchemy adapters for authentication repository ports."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.auth.entities import AuthSession, AuthUser
from noesis.storage.postgres.models.auth import TUser, TUserSession


def user_from_orm(row: TUser) -> AuthUser:
    return AuthUser(
        id=row.id,
        username=row.username or "",
        password_hash=row.password or "",
        mobile=row.mobile,
        registration_invite_digest=row.registration_invite_digest,
        registration_invite_updated_at=row.registration_invite_updated_at,
        created_at=row.create_time,
        updated_at=row.update_time,
    )


def session_from_orm(row: TUserSession) -> AuthSession:
    return AuthSession(
        id=row.id,
        user_id=row.user_id,
        session_digest=row.session_digest,
        csrf_digest=row.csrf_digest,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
        revoked_at=row.revoked_at,
        device_name=row.device_name,
        user_agent_digest=row.user_agent_digest,
        last_ip=row.last_ip,
    )


class SqlAlchemyUserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: int) -> AuthUser | None:
        result = await self.db.execute(select(TUser).where(TUser.id == user_id))
        row = result.scalar_one_or_none()
        return user_from_orm(row) if row is not None else None

    async def get_by_username(self, username: str) -> AuthUser | None:
        result = await self.db.execute(select(TUser).where(TUser.username == username))
        row = result.scalar_one_or_none()
        return user_from_orm(row) if row is not None else None

    async def get_invite_owner(self) -> AuthUser | None:
        result = await self.db.execute(
            select(TUser).where(TUser.registration_invite_digest.is_not(None))
        )
        row = result.scalar_one_or_none()
        return user_from_orm(row) if row is not None else None

    async def add(self, user: AuthUser) -> AuthUser:
        row = TUser(
            username=user.username,
            password=user.password_hash,
            mobile=user.mobile,
            registration_invite_digest=user.registration_invite_digest,
            registration_invite_updated_at=user.registration_invite_updated_at,
            create_time=user.created_at,
            update_time=user.updated_at,
        )
        self.db.add(row)
        await self.db.flush()
        user.id = row.id
        return user

    async def save(self, user: AuthUser) -> None:
        if user.id is None:
            raise ValueError("cannot save user without id")
        await self.db.execute(
            update(TUser)
            .where(TUser.id == user.id)
            .values(
                username=user.username,
                password=user.password_hash,
                mobile=user.mobile,
                registration_invite_digest=user.registration_invite_digest,
                registration_invite_updated_at=user.registration_invite_updated_at,
                update_time=user.updated_at,
            )
        )


class SqlAlchemySessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_digest(self, session_digest: str) -> AuthSession | None:
        result = await self.db.execute(
            select(TUserSession).where(TUserSession.session_digest == session_digest)
        )
        row = result.scalar_one_or_none()
        return session_from_orm(row) if row is not None else None

    async def get_by_id_for_user(self, session_id: str, user_id: int) -> AuthSession | None:
        result = await self.db.execute(
            select(TUserSession).where(
                TUserSession.id == session_id,
                TUserSession.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        return session_from_orm(row) if row is not None else None

    async def add(self, session: AuthSession) -> AuthSession:
        self.db.add(TUserSession(**session.__dict__))
        await self.db.flush()
        return session

    async def save(self, session: AuthSession) -> None:
        await self.db.execute(
            update(TUserSession)
            .where(TUserSession.id == session.id)
            .values(
                csrf_digest=session.csrf_digest,
                last_seen_at=session.last_seen_at,
                idle_expires_at=session.idle_expires_at,
                revoked_at=session.revoked_at,
                device_name=session.device_name,
                user_agent_digest=session.user_agent_digest,
                last_ip=session.last_ip,
            )
        )

    async def revoke_all(self, user_id: int, revoked_at: int) -> None:
        await self.db.execute(
            update(TUserSession)
            .where(TUserSession.user_id == user_id, TUserSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )

    async def list_active(self, user_id: int, now_ms: int) -> list[AuthSession]:
        result = await self.db.execute(
            select(TUserSession)
            .where(
                TUserSession.user_id == user_id,
                TUserSession.revoked_at.is_(None),
                TUserSession.idle_expires_at > now_ms,
                TUserSession.absolute_expires_at > now_ms,
            )
            .order_by(TUserSession.last_seen_at.desc())
        )
        return [session_from_orm(row) for row in result.scalars()]
