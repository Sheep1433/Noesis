from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.domain.chat.runs import RunStatus
from noesis.repositories.agent_run_repository import AgentRunRepository


@pytest.mark.asyncio
async def test_finalize_only_accepts_first_terminal_writer() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=0)]
    )
    repository = AgentRunRepository(db)

    first = await repository.finalize(
        run_id="run-1",
        target=RunStatus.COMPLETED,
        assistant_status="completed",
        content={"parts": []},
        finished_at=1,
        finish_reason="stop",
    )
    second = await repository.finalize(
        run_id="run-1",
        target=RunStatus.PARTIAL,
        assistant_status="partial",
        content={"parts": []},
        finished_at=2,
        finish_reason="stopped",
    )

    assert first is True
    assert second is False
    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_finalize_requires_assistant_compare_and_set() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=0)]
    )
    repository = AgentRunRepository(db)

    with pytest.raises(RuntimeError, match="assistant terminal"):
        await repository.finalize(
            run_id="run-1",
            target=RunStatus.ERROR,
            assistant_status="error",
            content={"parts": []},
            finished_at=1,
            finish_reason="error",
        )
