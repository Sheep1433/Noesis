from datetime import datetime
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from schemas.login_vo import UserLogin, UserRegister, UserRegistrationRequest
from models.db_models import TUser
from exceptions.exception import ConflictException, LoginException
from common.logging import logger
from domain.auth.password import PwdUtil
from domain.auth.registration_invite import RegistrationInviteService


class LoginService:
    @classmethod
    async def register_with_invite(
        cls, query_db: AsyncSession, registration: UserRegistrationRequest
    ) -> TUser:
        result = await query_db.execute(
            select(TUser).where(TUser.username == registration.username)
        )
        if result.scalar_one_or_none():
            raise ConflictException(data="", message="用户名已存在")

        await RegistrationInviteService.verify(query_db, registration.invite_code)
        now = datetime.now()
        user = TUser(
            username=registration.username,
            password=PwdUtil.get_password_hash(registration.password),
            mobile=registration.mobile,
            create_time=now,
            update_time=now,
        )
        query_db.add(user)
        try:
            await query_db.commit()
        except IntegrityError as exc:
            await query_db.rollback()
            original = getattr(exc, "orig", None)
            if getattr(original, "sqlstate", None) == "23505" and getattr(original, "constraint_name", None) == "uq_t_user_username":
                logger.warning(f"注册用户名冲突: {registration.username}")
                raise ConflictException(data="", message="用户名已存在") from exc
            raise
        await query_db.refresh(user)
        logger.info(f"用户邀请码注册成功: username={user.username}, user_id={user.id}")
        return user

    @classmethod
    async def register_user(cls, query_db: AsyncSession, register_user: UserRegister) -> TUser:
        result = await query_db.execute(
            select(TUser).where(TUser.username == register_user.username)
        )
        if result.scalar_one_or_none():
            logger.warning(f'用户名已存在: {register_user.username}')
            raise ConflictException(data='', message='用户名已存在')

        now = datetime.now()
        user = TUser(
            username=register_user.username,
            password=PwdUtil.get_password_hash(register_user.password),
            mobile=register_user.mobile,
            create_time=now,
            update_time=now,
        )
        query_db.add(user)
        await query_db.commit()
        await query_db.refresh(user)
        logger.info(f'用户注册成功: {user.username}, user_id={user.id}')
        return user

    @classmethod
    async def authenticate_user(cls, request: Request, query_db: AsyncSession, login_user: UserLogin):
        result = await query_db.execute(select(TUser).where(TUser.username == login_user.username))
        user = result.scalar_one_or_none()
        if not user:
            logger.warning('用户不存在')
            raise LoginException(data='', message='用户不存在')
        logger.info(f"login_user->{login_user.username}, user_id->{user.id}")
        if not PwdUtil.verify_password(login_user.password, user.password):
            logger.warning('密码错误')
            raise LoginException(data='', message='密码错误')
        return user
