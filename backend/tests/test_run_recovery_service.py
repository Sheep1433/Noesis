from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis_server.services import run_recovery_service
from noesis_server.services.run_recovery_service import RunRecoveryService
from noesis_server.services.run_recovery_service import mark_running_tools_unknown


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
