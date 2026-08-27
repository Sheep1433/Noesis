import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import server.main as server_main
from noesis.services.subagent_session_service import SubagentSessionService


@asynccontextmanager
async def _session_context():
    yield MagicMock()


class _FakeLeadershipToken:
    def __init__(self, term: int = 1) -> None:
        self.term = term
        self.instance_id = "test-instance"
        self.cluster_id = "local"
        self.valid = True

    def require_valid(self) -> None:
        if not self.valid:
            raise RuntimeError("leadership lost")


class _FakeLeaderElector:
    """绕过真实 advisory lock / t_runtime_leader 的测试替身。"""

    def __init__(self, *, cluster_id: str) -> None:
        self.cluster_id = cluster_id
        self.instance_id = "test-instance"
        self._token: _FakeLeadershipToken | None = None
        self.release = AsyncMock()
        self.invalidate = MagicMock()

    @property
    def token(self) -> _FakeLeadershipToken | None:
        return self._token

    async def acquire(self) -> _FakeLeadershipToken:
        await pg_manager_for_tests.acquire_advisory_lock()
        self._token = _FakeLeadershipToken()
        return self._token


class _FakeRunDispatcher:
    def __init__(self, **_kwargs) -> None:
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.running = False


pg_manager_for_tests: MagicMock  # 由 _patch_lifespan_resources 注入


def _patch_lifespan_resources(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    global pg_manager_for_tests
    pg_manager = MagicMock()
    pg_manager.get_async_session_context.return_value = _session_context()
    pg_manager.close = AsyncMock()
    pg_manager.acquire_advisory_lock = AsyncMock()
    pg_manager.acquire_migration_lock = AsyncMock()
    pg_manager.release_migration_lock = AsyncMock()
    pg_manager.monitor_advisory_lock = AsyncMock()
    monkeypatch.setattr(server_main, "pg_manager", pg_manager)
    pg_manager_for_tests = pg_manager

    elector_holder: dict[str, _FakeLeaderElector] = {}

    def _fake_elector_factory(**kwargs):
        elector = _FakeLeaderElector(**kwargs)
        elector_holder["elector"] = elector
        return elector

    monkeypatch.setattr(server_main, "LeaderElector", _fake_elector_factory)
    monkeypatch.setattr(server_main, "RunDispatcher", _FakeRunDispatcher)

    async_names = (
        "init_database",
        "init_checkpointer",
        "close_checkpointer",
        "init_knowledge_base",
        "close_knowledge_base",
        "shutdown_sandboxes",
        "sync_existing_kb_collection_configs",
        "stop_scheduled_task_scheduler",
        "stop_telegram_runtime",
        "stop_feishu_runtime",
        "start_memory_sweeper",
        "start_memory_consolidator",
        "stop_memory_sweeper",
        "stop_memory_consolidator",
    )
    patched: dict[str, object] = {"pg_manager": pg_manager}
    for name in async_names:
        mock = AsyncMock(return_value=True)
        monkeypatch.setattr(server_main, name, mock)
        patched[name] = mock

    for name in (
        "start_scheduled_task_scheduler",
        "start_telegram_runtime",
        "start_feishu_runtime",
    ):
        mock = MagicMock()
        monkeypatch.setattr(server_main, name, mock)
        patched[name] = mock

    recover = AsyncMock()
    monkeypatch.setattr(server_main.RunRecoveryService, "recover_orphaned_runs", recover)
    patched["recover"] = recover
    reconcile_subagents = AsyncMock(return_value=0)
    monkeypatch.setattr(SubagentSessionService, "reconcile_orphaned_runs", reconcile_subagents)
    patched["reconcile_subagents"] = reconcile_subagents
    shutdown_run_manager = AsyncMock()
    monkeypatch.setattr(server_main.run_manager, "shutdown", shutdown_run_manager)
    patched["shutdown_run_manager"] = shutdown_run_manager
    return patched


@pytest.mark.asyncio
async def test_lifespan_releases_resources_when_app_body_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_lifespan_resources(monkeypatch)

    with pytest.raises(RuntimeError, match="request failed"):
        async with server_main.lifespan(server_main.app):
            raise RuntimeError("request failed")

    for name in (
        "stop_feishu_runtime",
        "stop_telegram_runtime",
        "stop_scheduled_task_scheduler",
        "shutdown_run_manager",
        "close_knowledge_base",
        "close_checkpointer",
        "shutdown_sandboxes",
    ):
        patched[name].assert_awaited_once()
    patched["pg_manager"].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_cleans_initialized_resources_when_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_lifespan_resources(monkeypatch)
    patched["init_knowledge_base"].side_effect = RuntimeError("qdrant init failed")

    with pytest.raises(RuntimeError, match="qdrant init failed"):
        async with server_main.lifespan(server_main.app):
            pass

    patched["close_checkpointer"].assert_awaited_once()
    patched["shutdown_sandboxes"].assert_awaited_once()
    patched["pg_manager"].close.assert_awaited_once()
    patched["close_knowledge_base"].assert_not_awaited()
    patched["stop_scheduled_task_scheduler"].assert_not_awaited()


@pytest.mark.asyncio
async def test_lifespan_does_not_start_runtime_when_owner_lock_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_lifespan_resources(monkeypatch)
    patched["pg_manager"].acquire_advisory_lock.side_effect = RuntimeError("lock held")

    with pytest.raises(RuntimeError, match="lock held"):
        async with server_main.lifespan(server_main.app):
            pass

    # 新契约（enable-distributed-sse-pubsub）：migration 先于执行锁（follower 也需完成
    # migration），获锁失败在 migration 之后 fail-fast；leader-only runtime 一律不启动
    patched["init_database"].assert_awaited_once()
    patched["recover"].assert_not_awaited()
    patched["start_scheduled_task_scheduler"].assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_stops_live_runs_when_owner_lock_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_lifespan_resources(monkeypatch)
    lock_lost = asyncio.Event()

    async def lose_lock() -> None:
        patched["pg_manager"].advisory_lock_ready = False
        lock_lost.set()

    patched["pg_manager"].monitor_advisory_lock.side_effect = lose_lock

    async with server_main.lifespan(server_main.app):
        await asyncio.wait_for(lock_lost.wait(), timeout=1)
        await asyncio.sleep(0)
        patched["shutdown_run_manager"].assert_awaited_once_with(drain_seconds=0)
