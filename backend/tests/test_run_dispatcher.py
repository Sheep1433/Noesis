"""Run dispatcher 契约：批量认领（SKIP LOCKED + 容量回调）、启动失败终态处理、唤醒兜底。

对应 openspec worker-role-split task 3.1/3.3（前身为
enable-distributed-sse-pubsub task 3.1–3.4，leader token 语义已移除）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.chat.runs.bus import InMemoryRunBus, WAKEUP_TOPIC_RUN_CREATED
from noesis.chat.runs.launch_payload import LaunchPayload
from noesis.services import run_dispatcher as run_dispatcher_module
from noesis.services.run_dispatcher import RunDispatcher


def _queued_run(run_id: str = "run-1", user_id: str = "user-1") -> SimpleNamespace:
    payload = LaunchPayload(
        schema_version=1,
        content="hello",
        qa_type="COMMON_QA",
        session_id="session-1",
        user_id=user_id,
        assistant_message_id="msg-1",
        origin="web",
        client_request_id="c-1",
        resolved_model="model-x",
        created_at=1,
        extra={"qa_type": "COMMON_QA"},
    )
    return SimpleNamespace(
        id=run_id,
        user_id=user_id,
        status="queued",
        owner_instance_id=None,
        owner_term=0,
        launch_payload=payload.to_dict(),
    )


class _DbContext:
    """替换 pg_manager.get_async_session_context 的假上下文。"""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_args):
        return False


@pytest.fixture
def wiring(monkeypatch):
    """组装 dispatcher 测试替身：repo / run service / user service。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)

    repository = MagicMock()
    repository.claim_next_batch = AsyncMock(return_value=[])
    repository.get = AsyncMock(return_value=None)

    started_runs: list[str] = []
    start_calls = AsyncMock()

    async def fake_start_queued_run(run, payload, current_user, **kwargs):
        started_runs.append(run.id)
        await start_calls(run, payload, current_user, **kwargs)

    finalize = AsyncMock()

    db = MagicMock()
    db.commit = AsyncMock()

    monkeypatch.setattr(
        run_dispatcher_module.pg_manager,
        "get_async_session_context",
        lambda: _DbContext(db),
    )
    monkeypatch.setattr(
        run_dispatcher_module, "AgentRunRepository", lambda _db: repository
    )
    monkeypatch.setattr(
        run_dispatcher_module.run_manager, "check_run_capacity", AsyncMock()
    )
    monkeypatch.setattr(
        run_dispatcher_module.RunService,
        "start_queued_run",
        fake_start_queued_run,
    )
    monkeypatch.setattr(
        run_dispatcher_module.RunService, "_finalize_start_failure", finalize
    )
    monkeypatch.setattr(
        run_dispatcher_module.UserService,
        "get_user_by_id",
        AsyncMock(return_value=MagicMock(user_id="user-1")),
    )

    def make_dispatcher(**overrides) -> RunDispatcher:
        kwargs = dict(
            bus=bus,
            instance_id="worker-a",
            scan_interval_seconds=60.0,
        )
        kwargs.update(overrides)
        return RunDispatcher(**kwargs)

    return SimpleNamespace(
        bus=bus,
        repository=repository,
        db=db,
        started_runs=started_runs,
        start_calls=start_calls,
        finalize=finalize,
        make_dispatcher=make_dispatcher,
        monkeypatch=monkeypatch,
    )


async def _trigger_scan(dispatcher: RunDispatcher) -> None:
    """直接驱动一轮补扫（绕过唤醒等待循环，保证确定性）。"""
    await dispatcher._scan_once()


@pytest.mark.asyncio
async def test_scan_claims_and_starts_with_epoch(wiring) -> None:
    run = _queued_run()
    wiring.repository.claim_next_batch.return_value = [("run-1", 3)]
    wiring.repository.get.return_value = run
    dispatcher = wiring.make_dispatcher()

    await _trigger_scan(dispatcher)

    kwargs = wiring.repository.claim_next_batch.await_args.kwargs
    assert kwargs["owner_instance_id"] == "worker-a"
    assert isinstance(kwargs["limit"], int)
    assert isinstance(kwargs["now_ms"], int)
    # 容量检查经回调注入（锁内逐行判定的接线点）
    assert kwargs["capacity_check"] is run_dispatcher_module.run_manager.check_run_capacity
    # 认领上下文（owner + epoch）随启动传递——心跳与 fencing 的输入
    start_kwargs = wiring.start_calls.await_args.kwargs
    assert start_kwargs["owner_instance_id"] == "worker-a"
    assert start_kwargs["claim_epoch"] == 3
    assert wiring.started_runs == ["run-1"]
    wiring.db.commit.assert_awaited_once()
    wiring.finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_loser_batch_empty_skips_start(wiring) -> None:
    """并发 claim 输家（批量返回空）：跳过启动，无副作用。"""
    wiring.repository.claim_next_batch.return_value = []
    dispatcher = wiring.make_dispatcher()

    await _trigger_scan(dispatcher)

    assert wiring.started_runs == []


@pytest.mark.asyncio
async def test_start_failure_after_claim_is_finalized(wiring) -> None:
    """claim 成功但 producer 启动失败：必须标记终态，不留无 producer 的行。"""
    run = _queued_run()
    wiring.repository.claim_next_batch.return_value = [("run-1", 1)]
    wiring.repository.get.return_value = run

    async def boom(*_args, **_kwargs):
        raise RuntimeError("cannot register producer")

    wiring.monkeypatch.setattr(
        run_dispatcher_module.RunService, "start_queued_run", boom
    )
    dispatcher = wiring.make_dispatcher()

    await _trigger_scan(dispatcher)

    wiring.finalize.assert_awaited_once_with(run)


@pytest.mark.asyncio
async def test_context_rebuild_failure_finalizes(wiring) -> None:
    """payload 损坏 / 用户已删除：claim 已提交，标记为 RUN_START_FAILED。"""
    run = _queued_run()
    run.launch_payload = {"schema_version": 99}
    wiring.repository.claim_next_batch.return_value = [("run-1", 1)]
    wiring.repository.get.return_value = run
    dispatcher = wiring.make_dispatcher()

    await _trigger_scan(dispatcher)

    assert wiring.started_runs == []
    wiring.finalize.assert_awaited_once_with(run)


@pytest.mark.asyncio
async def test_wakeup_loss_recovered_by_scan(wiring) -> None:
    """唤醒丢失兜底：不依赖 wakeup，周期补扫也能启动 queued run。"""
    run = _queued_run()
    wiring.repository.claim_next_batch.return_value = [("run-1", 1)]
    wiring.repository.get.return_value = run
    dispatcher = wiring.make_dispatcher(scan_interval_seconds=0.05)

    await dispatcher.start()
    # 不发 wakeup；等待补扫周期触发
    for _ in range(100):
        if wiring.started_runs:
            break
        await asyncio.sleep(0.05)
    await dispatcher.stop()

    assert wiring.started_runs == ["run-1"]


@pytest.mark.asyncio
async def test_wakeup_triggers_immediate_scan(wiring) -> None:
    run = _queued_run()
    wiring.repository.claim_next_batch.return_value = [("run-1", 1)]
    wiring.repository.get.return_value = run
    dispatcher = wiring.make_dispatcher(scan_interval_seconds=60.0)

    await dispatcher.start()
    await wiring.bus.wakeup(WAKEUP_TOPIC_RUN_CREATED, {"run_id": "run-1"})
    for _ in range(100):
        if wiring.started_runs:
            break
        await asyncio.sleep(0.02)
    await dispatcher.stop()

    assert wiring.started_runs == ["run-1"]


@pytest.mark.asyncio
async def test_no_double_start_on_idempotent_scan(wiring) -> None:
    """同一 run 重复出现（已被认领）：批量返回空，不双启动。"""
    run = _queued_run()
    wiring.repository.claim_next_batch.side_effect = [[("run-1", 1)], []]
    wiring.repository.get.return_value = run
    dispatcher = wiring.make_dispatcher()

    await _trigger_scan(dispatcher)
    await _trigger_scan(dispatcher)

    assert wiring.started_runs == ["run-1"]
