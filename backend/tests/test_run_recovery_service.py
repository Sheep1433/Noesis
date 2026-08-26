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
        assistant_message_id="assistant-1",
        snapshot={"parts": [{"type": "text", "content": "Useful partial result"}]},
        last_sequence=3,
        status="running",
        owner_instance_id="dead-instance",
        owner_term=0,
    )
    message = SimpleNamespace(content=run.snapshot)
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
        assistant_message_id="assistant-queued",
        snapshot={"parts": []},
        last_sequence=0,
        status="queued",
        owner_instance_id=None,
        owner_term=0,
    )
    running_old_term = SimpleNamespace(
        id="run-running",
        assistant_message_id="assistant-running",
        snapshot={"parts": []},
        last_sequence=5,
        status="running",
        owner_instance_id="dead-instance",
        owner_term=2,
    )
    message_result = MagicMock()
    message_result.scalar_one_or_none.return_value = SimpleNamespace(
        content={"parts": []}
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
