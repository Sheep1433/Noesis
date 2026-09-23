"""命令消费分片契约（worker-role-split task 4.1/4.2）。

shard_filter：worker 只认领「命令目标在本进程持有」的命令；未持有的
跳过（不认领、不执行），留给目标 owner worker 的扫描或换主对账。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.services.run_command_service import RunCommandConsumer


@pytest.fixture
def consumer() -> RunCommandConsumer:
    return RunCommandConsumer(bus=None, scan_interval_seconds=5.0, retention_days=7.0)


def _cmd(cmd_id: str, run_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(id=cmd_id, type="stop", run_id=run_id, user_id="u1", payload={})


@pytest.mark.asyncio
async def test_shard_filter_claims_only_owned_runs(consumer, monkeypatch) -> None:
    """本进程持有的 run 命令认领执行；他进程的跳过（不认领不执行）。"""
    owned = {"run-mine"}
    shard = lambda row: row.run_id in owned  # noqa: E731
    consumer._shard_filter = shard

    repo = MagicMock()
    # 圈行返回两条；shard 过滤后只认领 run-mine
    claimed_rows: list = []

    async def fake_claim_pending(*, limit=20, shard_filter=None):
        rows = [_cmd("c-1", "run-mine"), _cmd("c-2", "run-other")]
        if shard_filter is not None:
            rows = [r for r in rows if shard_filter(r)]
        claimed_rows.extend(rows)
        return rows

    repo.claim_pending = fake_claim_pending
    repo.reset_stale_claimed = AsyncMock()
    executed: list = []

    async def fake_execute(row):
        executed.append(row.id)

    consumer._execute = fake_execute
    monkeypatch.setattr(
        "noesis.services.run_command_service.AgentRunCommandRepository",
        lambda _db: repo,
    )
    monkeypatch.setattr(
        "noesis.services.run_command_service.pg_manager",
        SimpleNamespace(get_async_session_context=lambda: _null_ctx()),
    )

    count = await consumer._consume_once()

    assert count == 1
    assert executed == ["c-1"]  # 只执行本进程持有的
    assert [r.id for r in claimed_rows] == ["c-1"]


@pytest.mark.asyncio
async def test_shard_filter_none_claims_all(consumer, monkeypatch) -> None:
    """不过滤（单进程/control 全局消费）：全部认领执行。"""
    repo = MagicMock()
    executed: list = []

    async def fake_claim_pending(*, limit=20, shard_filter=None):
        rows = [_cmd("c-1", "run-a"), _cmd("c-2", "run-b")]
        if shard_filter is not None:
            rows = [r for r in rows if shard_filter(r)]
        return rows

    repo.claim_pending = fake_claim_pending
    repo.reset_stale_claimed = AsyncMock()

    async def fake_execute(row):
        executed.append(row.id)

    consumer._execute = fake_execute
    monkeypatch.setattr(
        "noesis.services.run_command_service.AgentRunCommandRepository",
        lambda _db: repo,
    )
    monkeypatch.setattr(
        "noesis.services.run_command_service.pg_manager",
        SimpleNamespace(get_async_session_context=lambda: _null_ctx()),
    )

    assert await consumer._consume_once() == 2
    assert executed == ["c-1", "c-2"]


class _null_ctx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return False
