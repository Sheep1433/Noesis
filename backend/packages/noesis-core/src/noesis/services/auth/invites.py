"""Registration invite application service."""

from __future__ import annotations

import secrets
import time

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.auth.policy import digest_secret, verify_invite_digest
from noesis.auth.ports import UserRepository
from noesis.errors.exceptions import LoginException
from noesis.repositories.auth_repository import SqlAlchemyUserRepository


def _now_ms() -> int:
    return int(time.time() * 1000)


class RegistrationInviteService:
    @staticmethod
    def _repository(db: AsyncSession) -> UserRepository:
        return SqlAlchemyUserRepository(db)

    @staticmethod
    def _new_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @classmethod
    async def rotate(cls, db: AsyncSession, admin_username: str = "admin") -> str:
        repository = cls._repository(db)
        admin = await repository.get_by_username(admin_username)
        if admin is None:
            raise ValueError(f"管理员用户不存在: {admin_username}")
        code = cls._new_code()
        admin.registration_invite_digest = digest_secret(code)
        admin.registration_invite_updated_at = _now_ms()
        await repository.save(admin)
        await db.commit()
        return code

    @classmethod
    async def verify(cls, db: AsyncSession, code: str) -> None:
        owner = await cls._repository(db).get_invite_owner()
        if owner is None or not verify_invite_digest(owner.registration_invite_digest, code):
            raise LoginException(data="", message="邀请码无效")
