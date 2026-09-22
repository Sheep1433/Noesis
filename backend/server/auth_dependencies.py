"""HTTP 认证依赖：Cookie Session、当前用户与 CSRF 校验。"""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import SessionConfig
from noesis.errors.exceptions import AuthException, PermissionException
from noesis.schemas.login_vo import CurrentUser
from noesis.runtime.logging import logger
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


async def require_csrf(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """写请求的 CSRF 依赖（路由器级挂载，单一实现）。

    携带 session cookie 的写请求必须带有效 X-CSRF-Token（会话自取，
    不依赖 get_current_user 先行填充）；无 cookie 的请求放行——认证
    约束由各端点的 get_current_user 负责，CSRF 只在"已认证会话被
    跨站冒用"这一威胁面上有意义。豁免端点（登录/注册：用户可能持有
    旧 session cookie 但无法提供新 token）经 router 级 dependencies
    覆盖表达。
    """
    raw_session = request.cookies.get(SessionConfig.cookie_name)
    if not raw_session:
        return
    session = await SessionService.get_valid(db, raw_session)
    if session is None:
        return
    token = request.headers.get("X-CSRF-Token")
    if not SessionService.verify_csrf(session, token):
        logger.warning(
            "csrf_rejected path={} session_id={} user_id={}",
            request.url.path,
            session.id,
            session.user_id,
        )
        raise PermissionException(
            data="",
            message="会话验证失败，请刷新页面后重试",
        )

