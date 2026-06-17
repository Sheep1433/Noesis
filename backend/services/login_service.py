from datetime import timedelta, timezone, datetime
from typing import Union

import jwt
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from schemas.login_vo import UserLogin, UserRegister
from models.db_models import TUser
from exceptions.exception import ConflictException, LoginException
from common.logging import logger
from domain.auth.password import PwdUtil
from config.env import JwtConfig


class LoginService:
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

    @classmethod
    async def create_access_token(cls, data: dict, expires_delta: Union[timedelta, None] = None):
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=30)
        to_encode.update({'exp': expire})
        encoded_jwt = jwt.encode(to_encode, JwtConfig.jwt_secret_key, algorithm=JwtConfig.jwt_algorithm)
        return encoded_jwt

