"""由管理员用户持有的全局、可轮换注册码。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from exceptions.exception import LoginException
from models.db_models import TUser


def _now_ms() -> int:
    return int(time.time() * 1000)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RegistrationInviteService:
    @staticmethod
    def _new_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @classmethod
    async def rotate(cls, db: AsyncSession, admin_username: str = "admin") -> str:
        result = await db.execute(select(TUser).where(TUser.username == admin_username))
        admin = result.scalar_one_or_none()
        if admin is None:
            raise ValueError(f"管理员用户不存在: {admin_username}")
        code = cls._new_code()
        admin.registration_invite_digest = _digest(code)
        admin.registration_invite_updated_at = _now_ms()
        await db.commit()
        return code

    @classmethod
    async def verify(cls, db: AsyncSession, code: str) -> None:
        result = await db.execute(
            select(TUser).where(TUser.registration_invite_digest.is_not(None))
        )
        owner = result.scalar_one_or_none()
        if owner is None or not hmac.compare_digest(owner.registration_invite_digest, _digest(code)):
            raise LoginException(data="", message="邀请码无效")
