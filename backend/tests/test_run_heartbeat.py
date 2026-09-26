"""worker 心跳协程契约（worker-role-split task 3.4）。

三条退出路径：失去持有（heartbeat 落空 → 停本地 run）、run 终态、
run 从内存运行时回收；DB 抖动不误判（下轮重试）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.chat.runs import RunStatus
from noesis.services import run_service as run_service_module
from noesis.services.run_service import RunService


class _DbContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_args):
        return False


def _handle(status: RunStatus) -> SimpleNamespace:
    return SimpleNamespace(status=status)


@pytest.fixture(autouse=True)
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(RunService, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.mark.asyncio
async def test_lease_lost_stops_local_run(monkeypatch) -> None:
    """心跳落空（run 被重置/再认领）：停本地执行并退出协程。"""
    handle = _handle(RunStatus.RUNNING)
    stop = AsyncMock()
    heartbeat = AsyncMock(side_effect=[True, False])  # 第二轮落空
    repository = MagicMock()
    repository.heartbeat = heartbeat
    db = MagicMock()
    db.commit = AsyncMock()

    monkeypatch.setattr(run_service_module.run_manager, "get", MagicMock(return_value=handle))
    monkeypatch.setattr(run_service_module.run_manager, "stop", stop)
    monkeypatch.setattr(
        run_service_module.pg_manager, "get_async_session_context",
        lambda: _DbContext(db),
    )
    monkeypatch.setattr(run_service_module, "AgentRunRepository", lambda _db: repository)

    RunService._spawn_heartbeat("run-1", "worker-1", 2)
    assert await _wait_until(lambda: stop.await_count == 1)
    # 停后不再心跳（协程退出）
    calls_after_stop = heartbeat.await_count
    await asyncio.sleep(0.05)
    assert heartbeat.await_count == calls_after_stop


@pytest.mark.asyncio
async def test_terminal_run_exits_heartbeat(monkeypatch) -> None:
    """run 已终态：心跳协程静默退出，不写心跳、不停 run。"""
    handle = _handle(RunStatus.COMPLETED)
    stop = AsyncMock()
    repository = MagicMock()
    repository.heartbeat = AsyncMock()

    monkeypatch.setattr(run_service_module.run_manager, "get", MagicMock(return_value=handle))
    monkeypatch.setattr(run_service_module.run_manager, "stop", stop)
    monkeypatch.setattr(
        run_service_module.pg_manager, "get_async_session_context",
        lambda: _DbContext(MagicMock()),
    )
    monkeypatch.setattr(run_service_module, "AgentRunRepository", lambda _db: repository)

    RunService._spawn_heartbeat("run-1", "worker-1", 2)
    await asyncio.sleep(0.05)
    repository.heartbeat.assert_not_awaited()
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_db_error_does_not_kill_heartbeat(monkeypatch) -> None:
    """DB 抖动（心跳写失败）≠ 失去持有：下轮重试，不停 run。"""
    handle = _handle(RunStatus.RUNNING)
    stop = AsyncMock()
    heartbeat = AsyncMock(side_effect=[RuntimeError("db down"), True, True])
    repository = MagicMock()
    repository.heartbeat = heartbeat
    db = MagicMock()
    db.commit = AsyncMock()

    monkeypatch.setattr(run_service_module.run_manager, "get", MagicMock(return_value=handle))
    monkeypatch.setattr(run_service_module.run_manager, "stop", stop)
    monkeypatch.setattr(
        run_service_module.pg_manager, "get_async_session_context",
        lambda: _DbContext(db),
    )
    monkeypatch.setattr(run_service_module, "AgentRunRepository", lambda _db: repository)

    RunService._spawn_heartbeat("run-1", "worker-1", 2)
    assert await _wait_until(lambda: heartbeat.await_count >= 2)
    await asyncio.sleep(0.02)
    stop.assert_not_awaited()  # 抖动后恢复心跳，未误判失去持有
