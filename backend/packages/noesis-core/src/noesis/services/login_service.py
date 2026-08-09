from datetime import datetime
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from noesis.schemas.login_vo import UserLogin, UserRegister, UserRegistrationRequest
from noesis.domain.auth.entities import AuthUser
from noesis.errors.exceptions import ConflictException, LoginException
from noesis.repositories.auth_repository import SqlAlchemyUserRepository
from noesis.runtime.logging import logger
from noesis.domain.auth.password import PwdUtil
from noesis.services.auth.invites import RegistrationInviteService


class LoginService:
    @classmethod
    async def register_with_invite(
        cls, query_db: AsyncSession, registration: UserRegistrationRequest
    ) -> AuthUser:
        repository = SqlAlchemyUserRepository(query_db)
        if await repository.get_by_username(registration.username):
            raise ConflictException(data="", message="用户名已存在")

        await RegistrationInviteService.verify(query_db, registration.invite_code)
        now = datetime.now()
        user = AuthUser(
            id=None,
            username=registration.username,
            password_hash=PwdUtil.get_password_hash(registration.password),
            mobile=registration.mobile,
            created_at=now,
            updated_at=now,
        )
        try:
            await repository.add(user)
            await query_db.commit()
        except IntegrityError as exc:
            await query_db.rollback()
            original = getattr(exc, "orig", None)
            if getattr(original, "sqlstate", None) == "23505" and getattr(original, "constraint_name", None) == "uq_t_user_username":
                logger.warning(f"注册用户名冲突: {registration.username}")
                raise ConflictException(data="", message="用户名已存在") from exc
            raise
        logger.info(f"用户邀请码注册成功: username={user.username}, user_id={user.id}")
        return user

    @classmethod
    async def register_user(cls, query_db: AsyncSession, register_user: UserRegister) -> AuthUser:
        repository = SqlAlchemyUserRepository(query_db)
        if await repository.get_by_username(register_user.username):
            logger.warning(f'用户名已存在: {register_user.username}')
            raise ConflictException(data='', message='用户名已存在')

        now = datetime.now()
        user = AuthUser(
            id=None,
            username=register_user.username,
            password_hash=PwdUtil.get_password_hash(register_user.password),
            mobile=register_user.mobile,
            created_at=now,
            updated_at=now,
        )
        await repository.add(user)
        await query_db.commit()
        logger.info(f'用户注册成功: {user.username}, user_id={user.id}')
        return user

    @classmethod
    async def authenticate_user(cls, request: Request, query_db: AsyncSession, login_user: UserLogin):
        user = await SqlAlchemyUserRepository(query_db).get_by_username(login_user.username)
        if not user:
            logger.warning('用户不存在')
            raise LoginException(data='', message='用户不存在')
        logger.info(f"login_user->{login_user.username}, user_id->{user.id}")
        if not PwdUtil.verify_password(login_user.password, user.password_hash):
            logger.warning('密码错误')
            raise LoginException(data='', message='密码错误')
        return user
