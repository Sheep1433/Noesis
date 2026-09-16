"""请求级日志上下文：每条日志带 request_id，一次请求的多条日志可串联。"""
from __future__ import annotations

import uuid

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestLogContextMiddleware(BaseHTTPMiddleware):
    """把 request_id 绑定进 loguru 上下文（contextvars），覆盖该请求链路上的所有日志。

    优先复用客户端/网关传入的 X-Request-ID，否则生成短 uuid；响应头回传便于对账。
    SSE 长连接期间 contextvars 随请求任务传播，后台 spawn 的子任务同样携带。
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", "").strip() or uuid.uuid4().hex[:12]
        with logger.contextualize(request_id=request_id):
            response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
