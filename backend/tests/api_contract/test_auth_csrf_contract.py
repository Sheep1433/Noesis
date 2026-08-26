"""认证与 CSRF 中间件 HTTP 契约。

单元测试只盖 policy/sessions 纯逻辑；cookie 解析、豁免路径、403/401 的
envelope 形状全部在中间件与依赖层，只有 TestClient 能验证。
背景：多窗口 CSRF 轮换 403 bug 曾在全部单测绿的情况下漏出。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from noesis.services.auth.sessions import SessionService

from .conftest import CSRF_PREV_TOKEN


def test_no_cookie_returns_401_envelope(anon_client) -> None:
    resp = anon_client.get("/api/chat/sessions")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == 401
    assert body["success"] is False
    assert "登录信息已过期" in body["msg"]


def test_invalid_cookie_returns_401_not_403(anon_client) -> None:
    # 带假 cookie：middleware 发现 session 无效后放行，由认证依赖统一 401
    anon_client.cookies.set("noesis_session", "garbage")
    resp = anon_client.get("/api/chat/sessions")
    assert resp.status_code == 401


def test_get_skips_csrf_middleware(anon_client) -> None:
    # GET 不进 CSRF 校验：无 token 也不会 403（仍是认证 401）
    resp = anon_client.get("/api/chat/runs/xxx")
    assert resp.status_code == 401


def test_exempt_path_login_does_not_require_csrf(anon_client) -> None:
    # login 在豁免列表：请求穿过 middleware 进入路由（此处卡在表单校验 422，而非 403）
    resp = anon_client.post("/api/auth/login", json={})
    assert resp.status_code == 422
    assert resp.status_code != 403


def test_write_without_csrf_token_returns_403(contract_client) -> None:
    contract_client.headers.pop("X-CSRF-Token", None)
    resp = contract_client.post("/api/auth/logout")
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == 403
    assert "会话验证失败" in body["msg"]


def test_write_with_wrong_csrf_token_returns_403(contract_client) -> None:
    contract_client.headers["X-CSRF-Token"] = "wrong-token"
    resp = contract_client.post("/api/auth/logout")
    assert resp.status_code == 403


def test_write_with_current_token_passes_middleware(contract_client) -> None:
    # 当前代 token 通过 middleware + require_csrf，logout 走完路由（revoke mock 后 200）
    with patch.object(SessionService, "revoke", AsyncMock()):
        resp = contract_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["code"] == 200


def test_write_with_previous_generation_token_still_valid(contract_client) -> None:
    """多窗口回归：新窗口触发 /auth/session 轮换后，旧窗口 token 一代内仍有效。"""
    contract_client.headers["X-CSRF-Token"] = CSRF_PREV_TOKEN
    with patch.object(SessionService, "revoke", AsyncMock()):
        resp = contract_client.post("/api/auth/logout")
    assert resp.status_code == 200
