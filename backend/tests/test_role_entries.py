"""三入口装配契约（worker-role-split Phase 3）。

单进程全干入口退役后的装配面断言：web 承载全部业务路由且挂 CSRF、
control/worker 仅健康探针 + 角色上报、memory bus fail-fast、
双角色对账组顺序（安全不变式）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_web_app_carries_business_routes_with_csrf() -> None:
    """web：全部业务路由在位，非豁免 router 统一挂 CSRF（挂载守卫的入口面）。"""
    from server.bootstrap.entries import build_web_app

    app = build_web_app()
    paths = {route.path for route in app.routes}
    assert "/api/chat/runs" in paths
    assert "/api/chat/sessions" in paths
    assert "/api/auth/login" in paths
    # CSRF 挂载守卫（tests/api_contract/test_csrf_mount_guard.py）在
    # server.main.app 上断言同一装配——web app 是其唯一来源


def test_control_and_worker_apps_are_health_only() -> None:
    """control / worker：仅健康探针（无业务路由——执行面不经 HTTP）。"""
    from server.bootstrap.entries import build_control_app, build_worker_app

    control = build_control_app()
    worker = build_worker_app()
    for app, role in ((control, "control"), (worker, "worker")):
        api_paths = [
            route.path
            for route in app.routes
            if route.path.startswith("/api")
        ]
        assert api_paths == [], f"{role} 不得承载业务路由: {api_paths}"
        assert any(route.path == "/health" for route in app.routes)


def test_memory_bus_fails_fast_for_role_entries() -> None:
    """三入口形态强制 redis bus：memory 模式启动即 ValueError。"""
    from types import SimpleNamespace

    from server.bootstrap import entries

    # frozen 单例不可写字段：保存原绑定后替换（finally 恢复）
    original = entries.DistributedRunsConfig
    entries.DistributedRunsConfig = SimpleNamespace(backend="memory")
    try:
        with pytest.raises(ValueError, match="redis"):
            entries._require_redis_bus()
    finally:
        entries.DistributedRunsConfig = original


def test_control_reconcile_order_matches_factory() -> None:
    """CONTROL_RECONCILE_ORDER（推导值）与步骤工厂产出一致——单一事实源。"""
    from server.bootstrap.leader_runtime import (
        CONTROL_RECONCILE_ORDER,
        _control_reconcile_steps,
    )

    steps = _control_reconcile_steps(MagicMock())
    assert [name for name, _ in steps] == CONTROL_RECONCILE_ORDER
    assert CONTROL_RECONCILE_ORDER == ["main_runs", "scheduled_task_runs"]


def test_worker_reconcile_order_matches_factory() -> None:
    """WORKER_RECONCILE_ORDER 与步骤工厂产出一致；命令重置先于消费者
    启动、queued 重建先于 dispatcher（顺序是安全不变式）。"""
    from server.bootstrap.leader_runtime import (
        WORKER_RECONCILE_ORDER,
        _worker_reconcile_steps,
    )

    steps = _worker_reconcile_steps(MagicMock())
    assert [name for name, _ in steps] == WORKER_RECONCILE_ORDER
    assert WORKER_RECONCILE_ORDER == [
        "subagent_runs",
        "shell_jobs",
        "rebuild_queued",
        "reset_claimed_commands",
        "restore_notifications",
    ]
    # 顺序不变式：claimed 重置在 queued 重建之后（重建入队的任务命令
    # 不被误翻转）
    assert WORKER_RECONCILE_ORDER.index("rebuild_queued") < WORKER_RECONCILE_ORDER.index(
        "reset_claimed_commands"
    )
