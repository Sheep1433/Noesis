from typing import Optional

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.get_db import get_db
from exceptions.exception import AuthException, LoginException
from models.db_models import TUser
from schemas.login_vo import CurrentUser
from schemas.qa_vo import QaStopRequest
from config.env import SessionConfig
from common.logging import logger
from domain.auth.password import PwdUtil
from domain.auth.session import SessionService


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
    def _attach_auth_state(cls, request: Request, current_user: CurrentUser) -> CurrentUser:
        request.state.auth_user = current_user
        return current_user

    @classmethod
    async def _user_from_id(cls, user_id: int, db: AsyncSession) -> CurrentUser:
        result = await db.execute(select(TUser).where(TUser.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthException(data='', message='用户不存在')
        return CurrentUser(user_id=user.id, username=user.username, mobile=user.mobile)

    @classmethod
    async def get_current_user(
        cls,
        http_request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        session = await SessionService.get_valid(db, http_request.cookies.get(SessionConfig.cookie_name))
        if session is None:
            raise AuthException(data='', message='登录信息已过期，访问系统资源失败')
        session = await SessionService.touch(db, session)
        current_user = await cls._user_from_id(session.user_id, db)
        http_request.state.auth_session = session
        http_request.state.csrf_token = None
        return cls._attach_auth_state(http_request, current_user)

    @classmethod
    async def require_csrf(cls, request: Request) -> None:
        session = getattr(request.state, "auth_session", None)
        token = request.headers.get("X-CSRF-Token")
        if session is None or not SessionService.verify_csrf(session, token):
            from exceptions.exception import PermissionException
            raise PermissionException(data='', message='CSRF 校验失败')

    @classmethod
    async def get_user_for_stop(
        cls,
        session_id: str,
        http_request: Request,
        stop_payload: QaStopRequest,
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        current_user = await cls.get_current_user(http_request, db)
        token = (http_request.headers.get("X-CSRF-Token") or stop_payload.csrf_token or "").strip()
        if not SessionService.verify_csrf(http_request.state.auth_session, token):
            from exceptions.exception import PermissionException
            raise PermissionException(data='', message='CSRF 校验失败')
        return current_user
