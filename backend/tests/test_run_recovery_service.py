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

    recovered = await RunRecoveryService.recover_orphaned_runs(db)

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

    recovered = await RunRecoveryService.recover_orphaned_runs(db)

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

    recovered = await RunRecoveryService.recover_orphaned_runs(
        db, current_leader_term=3
    )

    # 只收口旧任期 running run；queued 未 claim 的存活
    finalized_ids = [call.kwargs["run_id"] for call in repository.finalize.await_args_list]
    assert finalized_ids == ["run-running"]
    assert recovered == 1


@pytest.mark.asyncio
async def test_recovery_skips_current_term_runs(monkeypatch) -> None:
    """本任期 claim 的 Run 防御性跳过（新 leader 上任时不应出现）。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    current_term_run = SimpleNamespace(
        id="run-current",
        origin="web",
        assistant_message_id="assistant-current",
        snapshot={"parts": []},
        last_sequence=1,
        status="running",
        owner_instance_id="this-instance",
        owner_term=5,
    )
    orphan_result = MagicMock()
    orphan_result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=orphan_result)
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.list_non_terminal = AsyncMock(return_value=[current_term_run])
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "noesis.services.run_recovery_service.AgentRunRepository",
        lambda _db: repository,
    )

    recovered = await RunRecoveryService.recover_orphaned_runs(
        db, current_leader_term=5
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

    recovered = await RunRecoveryService.recover_orphaned_runs(db)

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

    recovered = await RunRecoveryService.recover_orphaned_runs(db)

    repository.finalize.assert_not_awaited()
    repository.finalize_run_only.assert_awaited_once()
    call = repository.finalize_run_only.await_args
    assert call.kwargs["run_id"] == "run-poison"
    assert call.kwargs["target"] is RunStatus.INTERRUPTED
    assert call.kwargs["finish_reason"] == "server_restart"
    assert call.kwargs["last_sequence"] == 4
    assert recovered == 1
    db.commit.assert_awaited_once()
