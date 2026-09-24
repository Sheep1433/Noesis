from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.services import run_recovery_service
from noesis.services.run_recovery_service import RunRecoveryService
from noesis.services.run_recovery_service import mark_running_tools_unknown


def test_recovery_marks_only_unfinished_tools_unknown() -> None:
    content = {
        "parts": [
            {"type": "text", "content": "已生成"},
            {"type": "tool", "name": "restart", "status": "running"},
            {"type": "tool", "name": "lookup", "status": "success", "output": "ok"},
        ]
    }

    recovered = mark_running_tools_unknown(content)

    assert recovered["parts"][0] == content["parts"][0]
    assert recovered["parts"][1]["outcome"] == "unknown"
    assert recovered["parts"][1]["status"] == "error"
    assert recovered["parts"][1]["state"] == "failed"
    assert recovered["parts"][1]["errorCategory"] == "server_restart"
    assert recovered["parts"][2]["status"] == "success"
    assert "outcome" not in recovered["parts"][2]


@pytest.mark.asyncio
async def test_recovery_closes_streaming_assistant_without_run(monkeypatch) -> None:
    message = SimpleNamespace(
        id="orphan-message",
        content={"parts": []},
        extra={"run_id": "deleted-run"},
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = [message]
    update_result = SimpleNamespace(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[orphan_result, update_result])
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[])
    monkeypatch.setattr(
        run_recovery_service,
        "AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(db, heartbeat_lease_ms=60_000)

    assert recovered == 1
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_finalizes_interrupted_run(monkeypatch) -> None:
    run = SimpleNamespace(
        id="run-1",
        origin="web",
        assistant_message_id="assistant-1",
        snapshot={"parts": [{"type": "text", "content": "Useful partial result"}]},
        last_sequence=3,
        status="running",
        owner_instance_id="dead-instance",
        owner_term=0,
        heartbeat_at=None,
    )
    message = SimpleNamespace(content=run.snapshot, status="streaming")
    message_result = MagicMock()
    message_result.scalar_one_or_none.return_value = message
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    delivery_update = SimpleNamespace(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[message_result, delivery_update, orphan_result])
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[run])
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(run_recovery_service, "AgentRunRepository", lambda _db: repository)

    recovered = await RunRecoveryService.recover_orphaned_runs(db, heartbeat_lease_ms=60_000)

    assert recovered == 1
    repository.finalize.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_keeps_unclaimed_queued_runs(monkeypatch) -> None:
    """未 claim 的 queued Run 跨重启存活（dispatcher 补扫启动），不被误杀。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    queued_unclaimed = SimpleNamespace(
        id="run-queued",
        origin="web",
        assistant_message_id="assistant-queued",
        snapshot={"parts": []},
        last_sequence=0,
        status="queued",
        owner_instance_id=None,
        owner_term=0,
        heartbeat_at=None,
    )
    running_old_term = SimpleNamespace(
        id="run-running",
        origin="web",
        assistant_message_id="assistant-running",
        snapshot={"parts": []},
        last_sequence=5,
        status="running",
        owner_instance_id="dead-instance",
        owner_term=2,
        heartbeat_at=None,
    )
    message_result = MagicMock()
    message_result.scalar_one_or_none.return_value = SimpleNamespace(
        content={"parts": []}, status="streaming"
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    delivery_update = SimpleNamespace(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[message_result, delivery_update, orphan_result])
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(
        return_value=[queued_unclaimed, running_old_term]
    )
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(db, heartbeat_lease_ms=60_000)

    # 只收口旧任期 running run；queued 未 claim 的存活
    finalized_ids = [call.kwargs["run_id"] for call in repository.finalize.await_args_list]
    assert finalized_ids == ["run-running"]
    assert recovered == 1


@pytest.mark.asyncio
async def test_recovery_skips_live_heartbeat_runs(monkeypatch) -> None:
    """心跳存活的 run 跳过（worker 正常执行中——周期对账的安全前提）。

    2026-09-23 实测教训：周期对账若按「已认领即孤儿」收口，control 每
    30s 误杀一批正在执行的 run。僵尸判定唯一依据是 heartbeat 超时。
    """
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    now_ms = int(time.time() * 1000)
    live_run = SimpleNamespace(
        id="run-live",
        origin="web",
        assistant_message_id="assistant-live",
        snapshot={"parts": []},
        last_sequence=1,
        status="running",
        owner_instance_id="healthy-worker",
        owner_term=5,
        heartbeat_at=now_ms - 5_000,  # 5 秒前刚心跳：租约内存活
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=orphan_result)
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[live_run])
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(
        db, heartbeat_lease_ms=60_000
    )

    repository.finalize.assert_not_awaited()
    assert recovered == 0


@pytest.mark.asyncio
async def test_recovery_skips_subagent_runs(monkeypatch) -> None:
    """子 Agent run 不走通用对账（统一由 reconcile_orphaned_runs 收口 ERROR）。"""
    subagent_run = SimpleNamespace(
        id="run-sub",
        origin="subagent",
        assistant_message_id="assistant-sub",
        snapshot={"parts": []},
        last_sequence=2,
        status="running",
        owner_instance_id=None,
        owner_term=0,
        heartbeat_at=None,
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=orphan_result)
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[subagent_run])
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(db, heartbeat_lease_ms=60_000)

    repository.finalize.assert_not_awaited()
    assert recovered == 0


@pytest.mark.asyncio
async def test_recovery_run_only_finalize_for_poisoned_message(monkeypatch) -> None:
    """毒丸数据回归：assistant 消息已终态而 run 遗留非终态——不炸启动，
    仅收口 run 行（完整 finalize 不调用，消息保持原终态不被覆盖）。"""
    from noesis.chat.runs import RunStatus
    poisoned_run = SimpleNamespace(
        id="run-poison",
        origin="web",
        assistant_message_id="assistant-poison",
        snapshot={"parts": []},
        last_sequence=4,
        status="running",
        owner_instance_id="dead-instance",
        owner_term=0,
        heartbeat_at=None,
    )
    # SELECT 消息：已终态 error（automation/channel 链路写入方只写了消息未收 run）
    message_result = MagicMock()
    message_result.scalar_one_or_none.return_value = SimpleNamespace(
        content={"parts": [{"type": "text", "content": "操作失败，请稍候重试"}]},
        status="error",
    )
    delivery_update = SimpleNamespace(rowcount=1)
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[message_result, delivery_update, orphan_result])
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[poisoned_run])
    repository.finalize = AsyncMock(return_value=True)
    repository.finalize_run_only = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(db, heartbeat_lease_ms=60_000)

    repository.finalize.assert_not_awaited()
    repository.finalize_run_only.assert_awaited_once()
    call = repository.finalize_run_only.await_args
    assert call.kwargs["run_id"] == "run-poison"
    assert call.kwargs["target"] is RunStatus.INTERRUPTED
    assert call.kwargs["finish_reason"] == "server_restart"
    assert call.kwargs["last_sequence"] == 4
    assert recovered == 1
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# 阶段化重置（worker-role-split Phase 1）：未碰世界的 run 重排队，epoch 保留
# ---------------------------------------------------------------------------


def _reset_table_update_result():
    """捕获 db.execute 收到的重置 UPDATE 语句，供断言 values。"""
    return MagicMock()


@pytest.mark.asyncio
async def test_recovery_resets_unstarted_run_to_queued(monkeypatch) -> None:
    """已 claim 但未产出任何事件的 run：重置 queued 等待再认领，不收口。"""
    unstarted = SimpleNamespace(
        id="run-unstarted",
        origin="web",
        assistant_message_id="assistant-unstarted",
        snapshot=None,
        last_sequence=0,
        status="running",
        owner_instance_id="dead-worker",
        owner_term=4,
        heartbeat_at=None,
        launch_payload={"run_id": "run-unstarted", "content": "hi"},
        claim_epoch=2,
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    reset_update = SimpleNamespace(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[reset_update, orphan_result])
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[unstarted])
    repository.finalize = AsyncMock()
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(
        db, heartbeat_lease_ms=60_000
    )

    assert recovered == 1
    assert repository.finalize.await_count == 0
    reset_stmt = db.execute.await_args_list[0].args[0]
    compiled = reset_stmt.compile()
    # 重置三件套：回 queued、清 owner/heartbeat；claim_epoch 不在 values（保留递增）
    assert compiled.params["status"] == "queued"
    assert compiled.params["owner_instance_id"] is None
    assert compiled.params["heartbeat_at"] is None
    assert "claim_epoch" not in compiled.params
    assert "claim_epoch" not in str(resize := reset_stmt) or "claim_epoch=(" in resize  # 仅出现在自增/条件，不作为赋值


@pytest.mark.asyncio
async def test_recovery_resets_skeleton_snapshot_run(monkeypatch) -> None:
    """create_run 落库即写 {"parts": []} 骨架——骨架不算碰世界，仍走重置。

    2026-09-23 实测回归：容器 truthy 判定让未启动 run 被误收口。
    """
    skeleton = SimpleNamespace(
        id="run-skeleton",
        origin="web",
        assistant_message_id="assistant-skeleton",
        snapshot={"parts": []},
        last_sequence=0,
        status="running",
        owner_instance_id="dead-worker",
        owner_term=4,
        launch_payload={"run_id": "run-skeleton", "content": "hi"},
        claim_epoch=2,
        heartbeat_at=None,
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    reset_update = SimpleNamespace(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[reset_update, orphan_result])
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[skeleton])
    repository.finalize = AsyncMock()
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(
        db, heartbeat_lease_ms=60_000
    )

    assert recovered == 1
    assert repository.finalize.await_count == 0
    reset_stmt = db.execute.await_args_list[0].args[0]
    assert reset_stmt.compile().params["status"] == "queued"


@pytest.mark.asyncio
async def test_recovery_does_not_reset_started_run(monkeypatch) -> None:
    """已产出事件（last_sequence>0）的 run：不重置，走收口 interrupted。"""
    started = SimpleNamespace(
        id="run-started",
        origin="web",
        assistant_message_id="assistant-started",
        snapshot={"parts": [{"type": "text", "content": "部分"}]},
        last_sequence=7,
        status="running",
        owner_instance_id="dead-worker",
        owner_term=4,
        launch_payload={"run_id": "run-started", "content": "hi"},
        claim_epoch=2,
    )
    message_result = MagicMock()
    message_result.scalar_one_or_none.return_value = SimpleNamespace(
        content={"parts": []}, status="streaming"
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    delivery_update = SimpleNamespace(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[message_result, delivery_update, orphan_result])
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[started])
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(
        db, heartbeat_lease_ms=60_000
    )

    assert recovered == 1
    repository.finalize.assert_awaited_once()
    # 第一条 db.execute 是消息 SELECT 而非重置 UPDATE（重置分支未进入）
    first_stmt = str(db.execute.await_args_list[0].args[0])
    assert "t_agent_run" not in first_stmt.lower() or "SELECT" in first_stmt.upper()


@pytest.mark.asyncio
async def test_recovery_does_not_reset_without_launch_payload(monkeypatch) -> None:
    """无 launch_payload 的未启动 run（无法重建 producer）：不重置，收口。"""
    broken = SimpleNamespace(
        id="run-broken",
        origin="web",
        assistant_message_id="assistant-broken",
        snapshot=None,
        last_sequence=0,
        status="running",
        owner_instance_id="dead-worker",
        owner_term=4,
        heartbeat_at=None,
        launch_payload=None,
        claim_epoch=1,
    )
    message_result = MagicMock()
    message_result.scalar_one_or_none.return_value = SimpleNamespace(
        content={"parts": []}, status="streaming"
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    delivery_update = SimpleNamespace(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[message_result, delivery_update, orphan_result])
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[broken])
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(
        db, heartbeat_lease_ms=60_000
    )

    assert recovered == 1
    repository.finalize.assert_awaited_once()
