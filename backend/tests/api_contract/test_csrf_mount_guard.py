"""CSRF 挂载守卫：全部写端点必须被路由器级 CSRF 依赖覆盖。

收敛背景（2026-09-21）：CSRF 曾有中间件 + 散点 require_csrf 双路径，
收敛为 main.py include_router 统一挂载 `Depends(require_csrf)`，唯一
豁免 auth_router（登录/注册需旧 cookie 可用；logout 类端点自声明）。
本测试防止未来新增 router 忘记进挂载列表、或端点绕过路由器级依赖。
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

import server.main as server_main
from server.auth_dependencies import require_csrf

_WRITE_METHODS = {"POST", "PUT", "DELETE"}

# 设计内豁免（auth_router 不挂全局依赖）：未认证可达的端点
_EXEMPT_PATHS = {"/api/auth/login", "/api/auth/register"}


def _iter_write_routes():
    for route in server_main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.methods & _WRITE_METHODS:
            yield route


def test_all_write_routes_covered_by_csrf_dependency() -> None:
    uncovered = []
    for route in _iter_write_routes():
        if route.path in _EXEMPT_PATHS:
            continue
        deps = [d.dependency for d in route.dependencies]
        if require_csrf not in deps:
            uncovered.append(f"{sorted(route.methods)[0]} {route.path}")
    assert not uncovered, f"写端点未被 CSRF 依赖覆盖: {uncovered}"


def test_exempt_auth_router_endpoints_declare_own_csrf() -> None:
    """豁免 router 的例外面：login/register 不需 CSRF（未认证，契约测试
    钉住无 token 可达），logout/logout-all/revoke 必须自声明依赖。"""
    write_auth = [r for r in _iter_write_routes() if r.path.startswith("/api/auth")]
    assert write_auth, "auth 写端点清单为空——挂载结构变化，请复核本测试"
    for route in write_auth:
        if route.path in _EXEMPT_PATHS:
            continue
        has_csrf = any(d.dependency is require_csrf for d in route.dependencies)
        assert has_csrf, f"{route.path} 在豁免 router 上但未自声明 require_csrf"


@pytest.mark.parametrize("method", sorted(_WRITE_METHODS))
def test_write_methods_exist(method: str) -> None:
    """哨兵：仓库存在该写方法的端点（防 _iter_write_routes 空转假绿）。"""
    assert any(method in r.methods for r in _iter_write_routes())
