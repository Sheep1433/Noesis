"""HTTP 认证依赖：Cookie Session、当前用户与 CSRF 校验。"""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import SessionConfig
from noesis.errors.exceptions import AuthException, PermissionException
from noesis.schemas.login_vo import CurrentUser
from noesis.services.auth.sessions import SessionService
from noesis.services.user_service import UserService
from server.db import get_db


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    session = await SessionService.get_valid(
        db,
        request.cookies.get(SessionConfig.cookie_name),
    )
    if session is None:
        raise AuthException(data="", message="登录信息已过期，访问系统资源失败")

    session = await SessionService.touch(db, session)
    current_user = await UserService.get_user_by_id(session.user_id, db)
    request.state.auth_session = session
    request.state.auth_user = current_user
    request.state.csrf_token = None
    return current_user


async def require_csrf(request: Request) -> None:
    session = getattr(request.state, "auth_session", None)
    token = request.headers.get("X-CSRF-Token")
    if session is None or not SessionService.verify_csrf(session, token):
        raise PermissionException(
            data="",
            message="会话验证失败，请刷新页面后重试",
        )
