"""Cookie Session 的全局 CSRF 校验。"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from server.response import ResponseUtil
from noesis.runtime.logging import logger
from noesis.storage.postgres.manager import pg_manager
from noesis.config.env import SessionConfig
from noesis.services.auth.sessions import SessionService

# 登录/注册等无需 CSRF 的 POST 路径白名单
_CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/user/logout",
}


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        # 登录/注册等路径豁免 CSRF（用户此时可能持有旧 session cookie 但无法提供 token）
        if request.url.path in _CSRF_EXEMPT_PATHS:
            return await call_next(request)
        raw_session = request.cookies.get(SessionConfig.cookie_name)
        if not raw_session:
            return await call_next(request)
        async with pg_manager.get_async_session_context() as db:
            session = await SessionService.get_valid(db, raw_session)
            if session is None:
                return await call_next(request)
            token = request.headers.get("X-CSRF-Token")
            if not SessionService.verify_csrf(session, token):
                # 403 属业务拒绝：不抛异常也应有迹可查，否则多窗口 token 失效无从排查
                logger.warning(
                    "csrf_rejected path={} session_id={} user_id={}",
                    request.url.path,
                    session.id,
                    session.user_id,
                )
                return ResponseUtil.forbidden(msg="会话验证失败，请刷新页面后重试")
        return await call_next(request)
