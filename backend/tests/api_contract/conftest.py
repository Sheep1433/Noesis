"""TestClient 级接口契约测试 fixtures。

与 ``tests/api/``（真实服务 + 真实 LLM，``-m integration`` 手动跑）相对：
这里在进程内挂载 FastAPI 应用，走真实 ``CsrfMiddleware → 认证依赖 → 路由 →
异常处理器 → ResponseUtil`` 全链路，仅 mock DB 与外部服务——中间件/路由/序列化
这些单元测试覆盖不到的层在这里断言。不标记 integration，随门禁常规跑。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from noesis.auth.entities import AuthSession
from noesis.auth.policy import digest_secret
from noesis.config.env import SessionConfig
from noesis.schemas.login_vo import CurrentUser
from noesis.services.auth.sessions import SessionService
from noesis.services.user_service import UserService
from server.api import chat_api
from server.db import get_db
from server.main import app

# 双代 CSRF token：当前代 + 轮换宽容的上一代（多窗口回归场景）
CSRF_TOKEN = "contract-csrf-current"
CSRF_PREV_TOKEN = "contract-csrf-prev"
_FAR_FUTURE_MS = 9_999_999_999_999


def build_contract_session() -> AuthSession:
    return AuthSession(
        id="sess-contract",
        user_id=1,
        session_digest=digest_secret("raw-session"),
        csrf_digest=digest_secret(CSRF_TOKEN),
        prev_csrf_digest=digest_secret(CSRF_PREV_TOKEN),
        created_at=1,
        last_seen_at=1,
        idle_expires_at=_FAR_FUTURE_MS,
        absolute_expires_at=_FAR_FUTURE_MS,
    )


def build_contract_user() -> CurrentUser:
    return CurrentUser(user_id="1", username="contract-user")


@asynccontextmanager
async def _null_db_ctx():
    yield AsyncMock()


def _override_db() -> None:
    async def _fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db


@pytest.fixture
def anon_client() -> Iterator[TestClient]:
    """未认证客户端：get_valid 返回 None，走真实 401 依赖路径。"""
    _override_db()
    try:
        with patch.object(
            SessionService, "get_valid", AsyncMock(return_value=None)
        ):
            client = TestClient(app)
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def contract_client() -> Iterator[TestClient]:
    """已认证客户端：cookie + 当前代 CSRF token，DB 与外部服务全部 mock。"""
    session = build_contract_session()
    _override_db()
    try:
        with (
            patch.object(SessionService, "get_valid", AsyncMock(return_value=session)),
            patch.object(SessionService, "touch", AsyncMock(return_value=session)),
            patch.object(UserService, "get_user_by_id", AsyncMock(return_value=build_contract_user())),
            # CSRF 中间件直接引用 pg_manager（非 Depends），必须单独 mock
            patch("server.middleware.csrf.pg_manager") as csrf_pg,
            # SSE 端点用 sse_prefetch_db 短命会话（非 Depends），同样单独 mock；
            # 传函数而非实例：每次调用生成全新 context manager，可重入
            patch.object(chat_api, "sse_prefetch_db", _null_db_ctx),
        ):
            csrf_pg.get_async_session_context.return_value = _null_db_ctx()
            client = TestClient(app)
            client.cookies.set(SessionConfig.cookie_name, "raw-session")
            client.headers["X-CSRF-Token"] = CSRF_TOKEN
            yield client
    finally:
        app.dependency_overrides.clear()
