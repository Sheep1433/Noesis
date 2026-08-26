from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.chat.runs import RunStatus
from noesis.repositories.agent_run_repository import AgentRunRepository


@pytest.mark.asyncio
async def test_finalize_only_accepts_first_terminal_writer(monkeypatch) -> None:
    db = MagicMock()
    # execute 序列：①首个 finalize 的 run UPDATE（rowcount=1）
    # ②旧 extra SELECT ③assistant UPDATE（rowcount=1）；④第二个 finalize 的 run UPDATE（rowcount=0）
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1, fetchone=lambda: None),
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=0),
        ]
    )
    repository = AgentRunRepository(db)

    first = await repository.finalize(
        run_id="run-1",
        target=RunStatus.COMPLETED,
        assistant_status="completed",
        content={"parts": []},
        last_sequence=1,
        finished_at=1,
        finish_reason="stop",
    )
    second = await repository.finalize(
        run_id="run-1",
        target=RunStatus.PARTIAL,
        assistant_status="partial",
        content={"parts": []},
        last_sequence=2,
        finished_at=2,
        finish_reason="stopped",
    )

    assert first is True
    assert second is False
    assert db.execute.await_count == 4


@pytest.mark.asyncio
async def test_finalize_requires_assistant_compare_and_set() -> None:
    db = MagicMock()
    # execute 序列：run UPDATE（rowcount=1）→ 旧 extra SELECT → assistant UPDATE（rowcount=0）
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1, fetchone=lambda: None),
            SimpleNamespace(rowcount=0),
        ]
    )
    repository = AgentRunRepository(db)

    with pytest.raises(RuntimeError, match="assistant terminal"):
        await repository.finalize(
            run_id="run-1",
            target=RunStatus.ERROR,
            assistant_status="error",
            content={"parts": []},
            last_sequence=1,
            finished_at=1,
            finish_reason="error",
        )


@pytest.mark.asyncio
async def test_stale_checkpoint_does_not_touch_assistant() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    repository = AgentRunRepository(db)

    stored = await repository.save_checkpoint(
        run_id="run-1",
        assistant_message_id="message-1",
        sequence=4,
        snapshot={"parts": []},
        content={"parts": []},
        attempt_id=1,
        status=RunStatus.RUNNING,
        finish_reason=None,
        updated_at=1,
    )

    assert stored is False
    assert db.execute.await_count == 1
