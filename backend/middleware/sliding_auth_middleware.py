"""鉴权成功后为响应附加滑动续期 Token（Header + HttpOnly Cookie）。"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from utils.auth_token_service import AuthTokenService


class SlidingAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        auth_user = getattr(request.state, "auth_user", None)
        if auth_user is None:
            return response
        if response.status_code >= 400:
            return response
        AuthTokenService.attach_sliding_refresh(
            response,
            int(auth_user.user_id),
            auth_user.username or "",
        )
        return response
