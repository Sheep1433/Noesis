from typing import Optional
from sqlalchemy import select
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from exceptions.exception import LoginException, AuthException
from utils.pwd_util import PwdUtil
from config.get_db import get_db
from model.db_models import TUser
from schemas.login_vo import CurrentUser
from utils.log_util import logger

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


class UserService:
    @classmethod
    async def authenticate_user(cls, username: str, password: str, db: AsyncSession) -> Optional[TUser]:
        result = await db.execute(select(TUser).where(TUser.user_name == username))
        user = result.scalar_one_or_none()
        if not user:
            logger.warning('用户不存在')
            raise LoginException(data='', message='用户不存在')
        if not PwdUtil.verify_password(password, user.password):
            logger.warning('用户名或密码错误')
            raise LoginException(data='', message='用户名或密码错误')
        return user

    @classmethod
    async def get_current_user(cls, token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
        payload = PwdUtil.decode_token(token)
        # logger.info(payload)
        user_id = int(payload.get('id'))
        if not user_id:
            logger.warning('用户token不合法')
            raise AuthException(data='', message='用户token不合法')
        result = await db.execute(select(TUser).where(TUser.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthException(data='', message='用户不存在')
        return CurrentUser(user_id=user.id, username=user.username, mobile=user.mobile)
