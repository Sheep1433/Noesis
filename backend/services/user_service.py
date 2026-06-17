from typing import Optional

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.get_db import get_db
from exceptions.exception import AuthException, LoginException
from models.db_models import TUser
from schemas.login_vo import CurrentUser
from schemas.qa_vo import QaStopRequest
from domain.auth.token_service import AUTH_COOKIE_NAME
from common.logging import logger
from domain.auth.password import PwdUtil
from domain.auth.stop_token import STOP_TOKEN_HEADER, StopTokenService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)


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
    def _resolve_access_token(cls, request: Request, bearer_token: Optional[str]) -> str:
        if bearer_token:
            return bearer_token
        cookie_token = request.cookies.get(AUTH_COOKIE_NAME)
        if cookie_token:
            return cookie_token
        logger.warning('未提供认证令牌')
        raise AuthException(data='', message='登录信息已过期，访问系统资源失败')

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
    async def _user_from_access_token(
        cls,
        request: Request,
        token: str,
        db: AsyncSession,
    ) -> CurrentUser:
        payload = PwdUtil.decode_token(token)
        user_id = int(payload.get('id'))
        if not user_id:
            logger.warning('用户token不合法')
            raise AuthException(data='', message='用户token不合法')
        current_user = await cls._user_from_id(user_id, db)
        return cls._attach_auth_state(request, current_user)

    @classmethod
    async def get_current_user(
        cls,
        http_request: Request,
        token: Optional[str] = Depends(oauth2_scheme),
        db: AsyncSession = Depends(get_db),
    ):
        raw_token = cls._resolve_access_token(http_request, token)
        return await cls._user_from_access_token(http_request, raw_token, db)

    @classmethod
    async def get_user_for_stop(
        cls,
        session_id: str,
        http_request: Request,
        stop_payload: QaStopRequest,
        token: Optional[str] = Depends(oauth2_scheme),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        """
        停止流式生成鉴权：优先 JWT（Bearer / HttpOnly Cookie），
        其次流式 message-start 下发的短期 stop_token。
        """
        access_token = token or http_request.cookies.get(AUTH_COOKIE_NAME)
        if access_token:
            try:
                return await cls._user_from_access_token(http_request, access_token, db)
            except AuthException:
                pass

        stop_token = (
            http_request.headers.get(STOP_TOKEN_HEADER) or stop_payload.stop_token or ""
        ).strip()
        if stop_token:
            user_id = StopTokenService.verify(session_id, stop_token)
            if user_id is not None:
                current_user = await cls._user_from_id(user_id, db)
                return cls._attach_auth_state(http_request, current_user)

        logger.warning(
            f'stop 鉴权失败 session_id={session_id} has_bearer={bool(token)} '
            f'has_cookie={bool(http_request.cookies.get(AUTH_COOKIE_NAME))} '
            f'has_stop_token={bool(stop_token)}'
        )
        raise AuthException(data='', message='登录信息已过期，访问系统资源失败')
