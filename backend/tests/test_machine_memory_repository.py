from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from noesis.repositories.machine_memory_repository import MachineMemoryRepository


@pytest.mark.asyncio
async def test_processing_health_aggregates_jobs_and_derived_view_lag() -> None:
    captured = datetime(2026, 8, 24, 1, tzinfo=timezone.utc)
    consolidated = datetime(2026, 8, 24, 2, tzinfo=timezone.utc)
    oldest = datetime(2026, 8, 24, 3, tzinfo=timezone.utc)
    newer = datetime(2026, 8, 24, 4, tzinfo=timezone.utc)
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            SimpleNamespace(all=lambda: [("pending", 2), ("dead", 1)]),
            SimpleNamespace(all=lambda: [
                ("workspace", "pending", 3, newer),
                ("workspace", "dead", 2, captured),
                ("index", "failed", 1, oldest),
            ]),
        ]),
        scalar=AsyncMock(side_effect=[captured, consolidated]),
    )

    health = await MachineMemoryRepository(db).processing_health("user-1")

    assert health.job_counts == {"pending": 2, "dead": 1}
    assert health.last_capture_at == captured
    assert health.last_consolidation_at == consolidated
    assert health.outbox_counts == {"workspace": 3, "index": 1}
    assert health.outbox_dead_counts == {"workspace": 2, "index": 0}
    assert health.oldest_outbox_at == oldest


@pytest.mark.asyncio
async def test_fenced_updates_reject_stale_claim_token() -> None:
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)),
    )
    repository = MachineMemoryRepository(db)

    assert not await repository.advance_claimed_job(
        job_id="job-1",
        claim_token="stale-token",
        phase="extract",
        stage_result={},
        coverage={},
    )
    assert not await repository.renew_job(
        job_id="job-1", claim_token="stale-token", lease_seconds=60
    )
    assert not await repository.finish_outbox(
        event_id="event-1", claim_token="stale-token"
    )
    assert not await repository.renew_outbox(
        event_id="event-1", claim_token="stale-token", lease_seconds=60
    )


@pytest.mark.asyncio
async def test_retry_at_attempt_limit_becomes_dead() -> None:
    job = SimpleNamespace(
        attempts=3,
        max_attempts=3,
        status="claimed",
        stage_result={},
        coverage={},
        error_summary=None,
        next_retry_at=None,
        claim_token="token-1",
        worker_id="worker-1",
        lease_until=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db = SimpleNamespace(flush=AsyncMock())
    repository = MachineMemoryRepository(db)
    repository.get_claimed_job = AsyncMock(return_value=job)  # type: ignore[method-assign]

    assert await repository.retry_claimed_job(
        job_id="job-1",
        claim_token="token-1",
        status="failed",
        stage_result={},
        coverage={},
        error_summary="timeout",
        retry_seconds=30,
    )
    assert job.status == "dead"
    assert job.next_retry_at is None
    assert job.claim_token is None


@pytest.mark.asyncio
async def test_exhausted_reapers_only_kill_expired_claims() -> None:
    statements = []

    async def execute(statement):
        statements.append(str(statement))
        return SimpleNamespace(rowcount=1)

    repository = MachineMemoryRepository(SimpleNamespace(execute=execute))

    assert await repository.reap_exhausted_jobs() == 1
    assert await repository.reap_exhausted_outbox() == 1

    job_sql, outbox_sql = statements
    assert "lease_until <" in job_sql
    assert "status IN" in job_sql
    assert "lease_until <" in outbox_sql
    assert "status =" in outbox_sql


@pytest.mark.asyncio
async def test_automatic_eligibility_excludes_low_trust_external_items() -> None:
    statements = []

    async def execute(statement):
        statements.append(str(statement))
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    repository = MachineMemoryRepository(SimpleNamespace(execute=execute))
    await repository.eligible_items_by_ids(
        user_id="00000000-0000-0000-0000-000000000001",
        scope_key="scope",
        memory_ids=["memory-1"],
    )

    assert "effective_provenance !=" in statements[0]
