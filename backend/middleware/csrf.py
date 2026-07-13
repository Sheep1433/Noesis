"""Cookie Session 的全局 CSRF 校验。"""
from __future__ import annotations

import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from common.http.response import ResponseUtil
from config.database import AsyncSessionLocal
from config.env import SessionConfig
from domain.auth.session import SessionService


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        raw_session = request.cookies.get(SessionConfig.cookie_name)
        if not raw_session:
            return await call_next(request)
        async with AsyncSessionLocal() as db:
            session = await SessionService.get_valid(db, raw_session)
            if session is None:
                return await call_next(request)
            token = request.headers.get("X-CSRF-Token")
            if not token and request.url.path.startswith("/api/chat/sessions/") and request.url.path.endswith("/stop"):
                try:
                    payload = json.loads((await request.body()) or b"{}")
                    token = payload.get("csrf_token")
                except (ValueError, TypeError):
                    token = None
            if not SessionService.verify_csrf(session, token):
                return ResponseUtil.forbidden(msg="CSRF 校验失败")
        return await call_next(request)
