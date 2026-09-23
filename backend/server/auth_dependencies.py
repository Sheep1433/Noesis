"""HTTP 认证依赖：Cookie Session、当前用户与 CSRF 校验。"""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.auth.entities import AuthSession
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
    # require_csrf（路由器级，先执行）已校验并缓存会话时复用——单请求单读。
    # isinstance 守卫：request.state 上非 AuthSession 的值（Mock/他处写入）
    # 一律视为无缓存，走正常 get_valid 路径。
    cached = getattr(request.state, "auth_session", None)
    session = cached if isinstance(cached, AuthSession) else None
    if session is None:
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

    携带 session cookie 的**写**请求必须带有效 X-CSRF-Token（会话自取，
    不依赖 get_current_user 先行填充）；无 cookie 的请求放行——认证
    约束由各端点的 get_current_user 负责，CSRF 只在"已认证会话被
    跨站冒用"这一威胁面上有意义。豁免端点（登录/注册：用户可能持有
    旧 session cookie 但无法提供新 token）经 router 级 dependencies
    覆盖表达。GET/HEAD/OPTIONS 一律跳过——路由器级依赖对全部方法
    执行，此处必须显式恢复旧中间件的安全方法语义（CSRF 防的是状态
    变更，GET 无需 token；回归见 test_get_with_session_but_no_token_not_rejected）。
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
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
    # 缓存给 get_current_user 复用（路由器级依赖先于端点级执行）：
    # 同一请求只查一次会话表（审计 P4 的双读问题）
    request.state.auth_session = session

