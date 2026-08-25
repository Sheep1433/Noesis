"""Shared authoritative queries for the new machine-memory model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.storage.postgres.models.memory import (
    TMemoryEvidence,
    TMemoryItem,
    TMemoryJob,
    TMemoryOutbox,
    TMemoryQueryTrace,
    TMemoryRelation,
    TMemoryRunSnapshot,
    TMemoryUserPreference,
)
from noesis.storage.postgres.models.chat import TAgentRun, TChatSession
from noesis.storage.postgres.models.chat import TChatMessage


@dataclass(frozen=True)
class MemoryProcessingHealth:
    job_counts: dict[str, int] = field(default_factory=dict)
    last_capture_at: datetime | None = None
    last_consolidation_at: datetime | None = None
    outbox_counts: dict[str, int] = field(default_factory=dict)
    outbox_dead_counts: dict[str, int] = field(default_factory=dict)
    oldest_outbox_at: datetime | None = None


@dataclass(frozen=True)
class CaptureContext:
    run_id: str
    user_id: str
    session_id: str
    session_kind: str
    qa_type: str
    origin: str
    status: str


@dataclass(frozen=True)
class CaptureSource:
    run: TAgentRun
    session: TChatSession
    user_message: TChatMessage | None
    assistant_message: TChatMessage


class MachineMemoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def capture_context(self, run_id: str) -> CaptureContext | None:
        row = (
            await self.db.execute(
                select(
                    TAgentRun.id,
                    TAgentRun.user_id,
                    TAgentRun.session_id,
                    TChatSession.kind,
                    TAgentRun.qa_type,
                    TAgentRun.origin,
                    TAgentRun.status,
                )
                .join(TChatSession, TChatSession.id == TAgentRun.session_id)
                .where(TAgentRun.id == run_id)
            )
        ).one_or_none()
        return CaptureContext(*row) if row is not None else None

    async def enqueue_capture_job(
        self, context: CaptureContext, *, max_attempts: int
    ) -> bool:
        result = await self.db.execute(
            pg_insert(TMemoryJob)
            .values(
                run_id=context.run_id,
                user_id=context.user_id,
                phase="capture",
                status="pending",
                max_attempts=max_attempts,
            )
            .on_conflict_do_nothing(index_elements=[TMemoryJob.run_id])
        )
        return bool(result.rowcount)

    async def load_capture_source(self, run_id: str) -> CaptureSource | None:
        run = await self.db.get(TAgentRun, run_id)
        if run is None:
            return None
        session = await self.db.get(TChatSession, run.session_id)
        assistant = await self.db.get(TChatMessage, run.assistant_message_id)
        if session is None or assistant is None:
            return None
        user_message = (
            await self.db.execute(
                select(TChatMessage)
                .where(
                    TChatMessage.session_id == run.session_id,
                    TChatMessage.user_id == run.user_id,
                    TChatMessage.role == "user",
                    TChatMessage.deleted_at.is_(None),
                    TChatMessage.message_sequence < assistant.message_sequence,
                )
                .order_by(TChatMessage.message_sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return CaptureSource(run, session, user_message, assistant)

    async def create_snapshot(
        self,
        *,
        run_id: str,
        user_id: str,
        session_id: str,
        scope_key: str,
        source_watermark: int,
        schema_version: str,
        content_digest: str,
        evidence_json: dict[str, Any],
        token_estimate: int,
        chunk_metadata: dict[str, Any],
        coverage: dict[str, Any],
    ) -> TMemoryRunSnapshot:
        await self.db.execute(
            pg_insert(TMemoryRunSnapshot)
            .values(
                run_id=run_id,
                user_id=user_id,
                session_id=session_id,
                scope_key=scope_key,
                source_updated_at=source_watermark,
                schema_version=schema_version,
                content_digest=content_digest,
                evidence_json=evidence_json,
                source_token_estimate=token_estimate,
                chunk_count=len(chunk_metadata.get("chunks") or []),
                chunk_metadata=chunk_metadata,
                coverage=coverage,
                capture_status="captured",
                processing_status="pending",
            )
            .on_conflict_do_nothing(index_elements=[TMemoryRunSnapshot.run_id])
        )
        snapshot = (
            await self.db.execute(
                select(TMemoryRunSnapshot).where(TMemoryRunSnapshot.run_id == run_id)
            )
        ).scalar_one()
        if snapshot.content_digest != content_digest:
            raise RuntimeError("immutable Run snapshot digest mismatch")
        return snapshot

    async def get_snapshot(self, snapshot_id: str) -> TMemoryRunSnapshot | None:
        return await self.db.get(TMemoryRunSnapshot, snapshot_id)

    async def set_snapshot_processing(
        self, snapshot_id: str, *, status: str, error_summary: str | None = None
    ) -> bool:
        result = await self.db.execute(
            update(TMemoryRunSnapshot)
            .where(TMemoryRunSnapshot.id == snapshot_id)
            .values(
                processing_status=status,
                error_summary=(error_summary or "")[:500] or None,
            )
        )
        return result.rowcount == 1

    async def claim_jobs(
        self, *, worker_id: str, limit: int, lease_seconds: float
    ) -> list[TMemoryJob]:
        now = datetime.now(timezone.utc)
        jobs = list(
            (
                await self.db.execute(
                    select(TMemoryJob)
                    .where(
                        TMemoryJob.attempts < TMemoryJob.max_attempts,
                        or_(
                            (
                                TMemoryJob.status.in_(("pending", "failed", "partial"))
                                & or_(
                                    TMemoryJob.next_retry_at.is_(None),
                                    TMemoryJob.next_retry_at <= now,
                                )
                            ),
                            (
                                (TMemoryJob.status == "claimed")
                                & (TMemoryJob.lease_until < now)
                            ),
                        ),
                    )
                    .order_by(TMemoryJob.created_at, TMemoryJob.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            job.status = "claimed"
            job.attempts += 1
            job.claimed_at = now
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.worker_id = worker_id
            job.claim_token = str(uuid.uuid4())
            job.updated_at = now
        await self.db.flush()
        return jobs

    async def get_claimed_job(
        self, *, job_id: str, claim_token: str, for_update: bool = False
    ) -> TMemoryJob | None:
        statement = select(TMemoryJob).where(
            TMemoryJob.id == job_id,
            TMemoryJob.status == "claimed",
            TMemoryJob.claim_token == claim_token,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def advance_claimed_job(
        self,
        *,
        job_id: str,
        claim_token: str,
        phase: str,
        stage_result: dict[str, Any],
        coverage: dict[str, Any],
        snapshot_id: str | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "phase": phase,
            "status": "pending",
            "stage_result": stage_result,
            "coverage": coverage,
            "claim_token": None,
            "worker_id": None,
            "lease_until": None,
            "next_retry_at": None,
            "error_summary": None,
            "updated_at": datetime.now(timezone.utc),
        }
        if snapshot_id is not None:
            values["snapshot_id"] = snapshot_id
        result = await self.db.execute(
            update(TMemoryJob)
            .where(
                TMemoryJob.id == job_id,
                TMemoryJob.status == "claimed",
                TMemoryJob.claim_token == claim_token,
            )
            .values(**values)
        )
        return result.rowcount == 1

    async def finish_claimed_job(
        self,
        *,
        job_id: str,
        claim_token: str,
        status: str,
        stage_result: dict[str, Any],
        coverage: dict[str, Any],
        error_summary: str | None = None,
    ) -> bool:
        result = await self.db.execute(
            update(TMemoryJob)
            .where(
                TMemoryJob.id == job_id,
                TMemoryJob.status == "claimed",
                TMemoryJob.claim_token == claim_token,
            )
            .values(
                status=status,
                stage_result=stage_result,
                coverage=coverage,
                claim_token=None,
                worker_id=None,
                lease_until=None,
                next_retry_at=None,
                error_summary=(error_summary or "")[:500] or None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        return result.rowcount == 1

    async def retry_claimed_job(
        self,
        *,
        job_id: str,
        claim_token: str,
        status: str,
        stage_result: dict[str, Any],
        coverage: dict[str, Any],
        error_summary: str,
        retry_seconds: float,
    ) -> bool:
        job = await self.get_claimed_job(
            job_id=job_id, claim_token=claim_token, for_update=True
        )
        if job is None:
            return False
        terminal_status = "dead" if job.attempts >= job.max_attempts else status
        job.status = terminal_status
        job.stage_result = stage_result
        job.coverage = coverage
        job.error_summary = error_summary[:500]
        job.next_retry_at = (
            None
            if terminal_status == "dead"
            else datetime.now(timezone.utc) + timedelta(seconds=retry_seconds)
        )
        job.claim_token = None
        job.worker_id = None
        job.lease_until = None
        job.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return True

    async def renew_job(
        self, *, job_id: str, claim_token: str, lease_seconds: float
    ) -> bool:
        result = await self.db.execute(
            update(TMemoryJob)
            .where(
                TMemoryJob.id == job_id,
                TMemoryJob.status == "claimed",
                TMemoryJob.claim_token == claim_token,
            )
            .values(
                lease_until=datetime.now(timezone.utc)
                + timedelta(seconds=lease_seconds),
                updated_at=datetime.now(timezone.utc),
            )
        )
        return result.rowcount == 1

    async def reap_exhausted_jobs(self) -> int:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(TMemoryJob)
            .where(
                or_(
                    TMemoryJob.status.in_(("pending", "failed", "partial")),
                    ((TMemoryJob.status == "claimed") & (TMemoryJob.lease_until < now)),
                ),
                TMemoryJob.attempts >= TMemoryJob.max_attempts,
            )
            .values(
                status="dead",
                claim_token=None,
                worker_id=None,
                lease_until=None,
                next_retry_at=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        return int(result.rowcount or 0)

    async def expired_snapshot_scopes(
        self, *, before: datetime
    ) -> list[tuple[str, str]]:
        return [
            (str(user_id), scope_key)
            for user_id, scope_key in (
                await self.db.execute(
                    select(
                        TMemoryRunSnapshot.user_id,
                        TMemoryRunSnapshot.scope_key,
                    )
                    .where(TMemoryRunSnapshot.captured_at < before)
                    .distinct()
                )
            ).all()
        ]

    async def cleanup_snapshots(
        self, *, before: datetime, user_id: str | None = None
    ) -> int:
        statement = delete(TMemoryRunSnapshot).where(
            TMemoryRunSnapshot.captured_at < before
        )
        if user_id is not None:
            statement = statement.where(TMemoryRunSnapshot.user_id == str(user_id))
        result = await self.db.execute(statement)
        return int(result.rowcount or 0)

    async def cleanup_terminal_jobs(self, *, before: datetime) -> int:
        result = await self.db.execute(
            delete(TMemoryJob).where(
                TMemoryJob.updated_at < before,
                TMemoryJob.status.in_(
                    ("succeeded", "succeeded_no_output", "dead", "skipped_disabled")
                ),
            )
        )
        return int(result.rowcount or 0)

    async def cleanup_terminal_outbox(self, *, before: datetime) -> int:
        result = await self.db.execute(
            delete(TMemoryOutbox).where(
                TMemoryOutbox.updated_at < before,
                TMemoryOutbox.status.in_(("succeeded", "dead")),
            )
        )
        return int(result.rowcount or 0)

    async def cleanup_query_traces(self, *, before: datetime) -> int:
        result = await self.db.execute(
            delete(TMemoryQueryTrace).where(TMemoryQueryTrace.created_at < before)
        )
        return int(result.rowcount or 0)

    async def lock_identity(self, lock_key: int) -> None:
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )

    async def get_current_item(
        self,
        *,
        user_id: str,
        scope_key: str,
        memory_type: str,
        subject_key: str,
        for_update: bool = False,
    ) -> TMemoryItem | None:
        statement = select(TMemoryItem).where(
            TMemoryItem.user_id == str(user_id),
            TMemoryItem.scope_key == scope_key,
            TMemoryItem.memory_type == memory_type,
            TMemoryItem.subject_key == subject_key,
            TMemoryItem.status != "superseded",
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def bounded_neighbors(
        self,
        *,
        user_id: str,
        scope_key: str,
        memory_type: str,
        subject: str,
        subject_key: str,
        limit: int = 5,
    ) -> list[TMemoryItem]:
        term = next((value for value in subject.split() if len(value) >= 2), subject)
        return list(
            (
                await self.db.execute(
                    select(TMemoryItem)
                    .where(
                        TMemoryItem.user_id == str(user_id),
                        TMemoryItem.scope_key == scope_key,
                        TMemoryItem.memory_type == memory_type,
                        TMemoryItem.subject_key != subject_key,
                        TMemoryItem.status.in_(("candidate", "active", "needs_review")),
                        or_(
                            TMemoryItem.subject.ilike(f"%{term}%"),
                            TMemoryItem.statement.ilike(f"%{term}%"),
                        ),
                    )
                    .order_by(
                        TMemoryItem.last_verified_at.desc().nullslast(), TMemoryItem.id
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    def add_item(self, item: TMemoryItem) -> None:
        self.db.add(item)

    async def add_evidence_if_missing(self, evidence: TMemoryEvidence) -> bool:
        existing = await self.db.scalar(
            select(TMemoryEvidence.id).where(
                TMemoryEvidence.memory_id == evidence.memory_id,
                TMemoryEvidence.snapshot_id == evidence.snapshot_id,
                TMemoryEvidence.source_ref == evidence.source_ref,
            )
        )
        if existing is not None:
            return False
        self.db.add(evidence)
        return True

    async def get_evidence_for_user(
        self,
        *,
        user_id: str,
        memory_id: str,
        evidence_id: str,
        scope_key: str | None = None,
    ) -> tuple[TMemoryItem, TMemoryEvidence] | None:
        statement = (
            select(TMemoryItem, TMemoryEvidence)
            .join(TMemoryEvidence, TMemoryEvidence.memory_id == TMemoryItem.id)
            .where(
                TMemoryItem.id == memory_id,
                TMemoryItem.user_id == str(user_id),
                TMemoryEvidence.id == evidence_id,
                TMemoryEvidence.user_id == str(user_id),
            )
        )
        if scope_key is not None:
            statement = statement.where(TMemoryItem.scope_key == scope_key)
        row = (await self.db.execute(statement)).one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def delete_user_data(self, user_id: str) -> None:
        uid = str(user_id)
        await self.db.execute(delete(TMemoryOutbox).where(TMemoryOutbox.user_id == uid))
        await self.db.execute(
            delete(TMemoryQueryTrace).where(TMemoryQueryTrace.user_id == uid)
        )
        await self.db.execute(delete(TMemoryJob).where(TMemoryJob.user_id == uid))
        await self.db.execute(delete(TMemoryItem).where(TMemoryItem.user_id == uid))
        await self.db.execute(
            delete(TMemoryRunSnapshot).where(TMemoryRunSnapshot.user_id == uid)
        )
        await self.db.execute(
            delete(TMemoryUserPreference).where(TMemoryUserPreference.user_id == uid)
        )

    def add_relation(self, relation: TMemoryRelation) -> None:
        self.db.add(relation)

    def add_desired_state_events(self, item: TMemoryItem) -> None:
        for target in ("workspace", "index"):
            self.db.add(
                TMemoryOutbox(
                    user_id=item.user_id,
                    scope_key=item.scope_key,
                    memory_id=item.id,
                    target=target,
                    desired_version=item.content_digest,
                )
            )

    def add_scope_rebuild_event(self, *, user_id: str, scope_key: str) -> None:
        self.db.add(
            TMemoryOutbox(
                user_id=str(user_id),
                scope_key=scope_key,
                memory_id=None,
                target="workspace",
                desired_version="retention-cleanup",
            )
        )

    async def list_scope_items(
        self, *, user_id: str, scope_key: str
    ) -> list[TMemoryItem]:
        return list(
            (
                await self.db.execute(
                    select(TMemoryItem)
                    .where(
                        TMemoryItem.user_id == str(user_id),
                        TMemoryItem.scope_key == scope_key,
                    )
                    .order_by(
                        TMemoryItem.memory_type,
                        TMemoryItem.subject_key,
                        TMemoryItem.version,
                    )
                )
            )
            .scalars()
            .all()
        )

    async def list_scope_snapshots(
        self, *, user_id: str, scope_key: str, limit: int = 100
    ) -> list[TMemoryRunSnapshot]:
        return list(
            (
                await self.db.execute(
                    select(TMemoryRunSnapshot)
                    .where(
                        TMemoryRunSnapshot.user_id == str(user_id),
                        TMemoryRunSnapshot.scope_key == scope_key,
                    )
                    .order_by(
                        TMemoryRunSnapshot.captured_at.desc(), TMemoryRunSnapshot.id
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def get_item(
        self, memory_id: str, *, user_id: str | None = None
    ) -> TMemoryItem | None:
        statement = select(TMemoryItem).where(TMemoryItem.id == memory_id)
        if user_id is not None:
            statement = statement.where(TMemoryItem.user_id == str(user_id))
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def get_item_for_update(
        self, *, user_id: str, memory_id: str
    ) -> TMemoryItem | None:
        return (
            await self.db.execute(
                select(TMemoryItem)
                .where(
                    TMemoryItem.id == memory_id,
                    TMemoryItem.user_id == str(user_id),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def list_user_items(
        self,
        *,
        user_id: str,
        statuses: tuple[str, ...] = (),
        memory_types: tuple[str, ...] = (),
        query: str = "",
        limit: int = 100,
    ) -> list[TMemoryItem]:
        statement = select(TMemoryItem).where(TMemoryItem.user_id == str(user_id))
        if statuses:
            statement = statement.where(TMemoryItem.status.in_(statuses))
        if memory_types:
            statement = statement.where(TMemoryItem.memory_type.in_(memory_types))
        if query.strip():
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    TMemoryItem.subject.ilike(pattern),
                    TMemoryItem.statement.ilike(pattern),
                    TMemoryItem.applicability.ilike(pattern),
                )
            )
        return list(
            (
                await self.db.execute(
                    statement.order_by(
                        TMemoryItem.updated_at.desc(), TMemoryItem.id
                    ).limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def list_item_evidence(
        self, *, user_id: str, memory_id: str, limit: int = 20
    ) -> list[TMemoryEvidence]:
        return list(
            (
                await self.db.execute(
                    select(TMemoryEvidence)
                    .where(
                        TMemoryEvidence.user_id == str(user_id),
                        TMemoryEvidence.memory_id == memory_id,
                    )
                    .order_by(TMemoryEvidence.created_at.desc(), TMemoryEvidence.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def list_evidence_by_items(
        self, *, user_id: str, memory_ids: list[str], per_item_limit: int = 20
    ) -> dict[str, list[TMemoryEvidence]]:
        if not memory_ids:
            return {}
        ranked = (
            select(
                TMemoryEvidence.id.label("evidence_id"),
                func.row_number()
                .over(
                    partition_by=TMemoryEvidence.memory_id,
                    order_by=(
                        TMemoryEvidence.created_at.desc(),
                        TMemoryEvidence.id,
                    ),
                )
                .label("evidence_rank"),
            )
            .where(
                TMemoryEvidence.user_id == str(user_id),
                TMemoryEvidence.memory_id.in_(memory_ids),
            )
            .subquery()
        )
        evidence = list(
            (
                await self.db.execute(
                    select(TMemoryEvidence)
                    .join(ranked, ranked.c.evidence_id == TMemoryEvidence.id)
                    .where(ranked.c.evidence_rank <= per_item_limit)
                    .order_by(
                        TMemoryEvidence.memory_id,
                        TMemoryEvidence.created_at.desc(),
                        TMemoryEvidence.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[str, list[TMemoryEvidence]] = {}
        for item in evidence:
            grouped.setdefault(item.memory_id, []).append(item)
        return grouped

    async def count_evidence_runs(
        self, *, user_id: str, memory_ids: list[str]
    ) -> dict[str, int]:
        if not memory_ids:
            return {}
        rows = (
            await self.db.execute(
                select(
                    TMemoryEvidence.memory_id,
                    func.count(func.distinct(TMemoryEvidence.run_id)),
                )
                .where(
                    TMemoryEvidence.user_id == str(user_id),
                    TMemoryEvidence.memory_id.in_(memory_ids),
                    TMemoryEvidence.run_id.is_not(None),
                )
                .group_by(TMemoryEvidence.memory_id)
            )
        ).all()
        return {memory_id: int(count) for memory_id, count in rows}

    async def delete_item(self, item: TMemoryItem) -> None:
        self.add_desired_state_events(item)
        await self.db.delete(item)

    async def list_active_items(
        self, *, offset: int = 0, limit: int = 500
    ) -> list[TMemoryItem]:
        return list(
            (
                await self.db.execute(
                    select(TMemoryItem)
                    .where(
                        TMemoryItem.status == "active", TMemoryItem.valid_to.is_(None)
                    )
                    .order_by(TMemoryItem.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def lexical_candidates(
        self, *, user_id: str, scope_key: str, query: str, limit: int
    ) -> list[TMemoryItem]:
        pattern = f"%{query.strip()}%"
        return list(
            (
                await self.db.execute(
                    select(TMemoryItem)
                    .where(
                        TMemoryItem.user_id == str(user_id),
                        TMemoryItem.scope_key == scope_key,
                        TMemoryItem.status == "active",
                        TMemoryItem.valid_to.is_(None),
                        TMemoryItem.effective_provenance != "tool_external",
                        or_(
                            TMemoryItem.subject.ilike(pattern),
                            TMemoryItem.statement.ilike(pattern),
                            TMemoryItem.applicability.ilike(pattern),
                        ),
                    )
                    .order_by(
                        TMemoryItem.last_verified_at.desc().nullslast(), TMemoryItem.id
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def eligible_items_by_ids(
        self, *, user_id: str, scope_key: str, memory_ids: list[str]
    ) -> list[TMemoryItem]:
        if not memory_ids:
            return []
        return list(
            (
                await self.db.execute(
                    select(TMemoryItem).where(
                        TMemoryItem.id.in_(memory_ids),
                        TMemoryItem.user_id == str(user_id),
                        TMemoryItem.scope_key == scope_key,
                        TMemoryItem.status == "active",
                        TMemoryItem.valid_to.is_(None),
                        TMemoryItem.effective_provenance != "tool_external",
                        select(TMemoryEvidence.id)
                        .where(TMemoryEvidence.memory_id == TMemoryItem.id)
                        .exists(),
                    )
                )
            )
            .scalars()
            .all()
        )

    async def search_items(
        self,
        *,
        user_id: str,
        scope_key: str,
        query: str,
        statuses: tuple[str, ...],
        memory_types: tuple[str, ...] = (),
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
    ) -> list[TMemoryItem]:
        pattern = f"%{query.strip()}%"
        statement = select(TMemoryItem).where(
            TMemoryItem.user_id == str(user_id),
            TMemoryItem.scope_key == scope_key,
            TMemoryItem.status.in_(statuses),
            or_(
                TMemoryItem.subject.ilike(pattern),
                TMemoryItem.statement.ilike(pattern),
                TMemoryItem.applicability.ilike(pattern),
            ),
        )
        if memory_types:
            statement = statement.where(TMemoryItem.memory_type.in_(memory_types))
        if since is not None:
            statement = statement.where(TMemoryItem.last_verified_at >= since)
        if until is not None:
            statement = statement.where(TMemoryItem.last_verified_at <= until)
        return list(
            (
                await self.db.execute(
                    statement.order_by(
                        TMemoryItem.last_verified_at.desc().nullslast(), TMemoryItem.id
                    ).limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def list_evidence_for_items(
        self,
        *,
        user_id: str,
        memory_ids: list[str],
        source_types: tuple[str, ...] = (),
        limit: int,
    ) -> list[TMemoryEvidence]:
        if not memory_ids:
            return []
        statement = (
            select(TMemoryEvidence)
            .where(
                TMemoryEvidence.user_id == str(user_id),
                TMemoryEvidence.memory_id.in_(memory_ids),
            )
            .order_by(TMemoryEvidence.created_at.desc(), TMemoryEvidence.id)
            .limit(limit)
        )
        if source_types:
            statement = statement.where(TMemoryEvidence.source_kind.in_(source_types))
        return list((await self.db.execute(statement)).scalars().all())

    async def source_types_for_items(
        self,
        *,
        user_id: str,
        memory_ids: list[str],
        source_types: tuple[str, ...],
    ) -> dict[str, set[str]]:
        if not memory_ids or not source_types:
            return {}
        rows = (
            await self.db.execute(
                select(TMemoryEvidence.memory_id, TMemoryEvidence.source_kind)
                .where(
                    TMemoryEvidence.user_id == str(user_id),
                    TMemoryEvidence.memory_id.in_(memory_ids),
                    TMemoryEvidence.source_kind.in_(source_types),
                )
                .distinct()
            )
        ).all()
        result: dict[str, set[str]] = {}
        for memory_id, source_kind in rows:
            result.setdefault(memory_id, set()).add(source_kind)
        return result

    async def claim_outbox(
        self, *, worker_id: str, limit: int, lease_seconds: float
    ) -> list[TMemoryOutbox]:
        now = datetime.now(timezone.utc)
        events = list(
            (
                await self.db.execute(
                    select(TMemoryOutbox)
                    .where(
                        TMemoryOutbox.attempts < TMemoryOutbox.max_attempts,
                        or_(
                            (
                                TMemoryOutbox.status.in_(("pending", "failed"))
                                & or_(
                                    TMemoryOutbox.next_retry_at.is_(None),
                                    TMemoryOutbox.next_retry_at <= now,
                                )
                            ),
                            (
                                (TMemoryOutbox.status == "claimed")
                                & (TMemoryOutbox.lease_until < now)
                            ),
                        ),
                    )
                    .order_by(TMemoryOutbox.created_at, TMemoryOutbox.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for event in events:
            event.status = "claimed"
            event.attempts += 1
            event.claimed_at = now
            event.lease_until = now + timedelta(seconds=lease_seconds)
            event.worker_id = worker_id
            event.claim_token = str(uuid.uuid4())
            event.updated_at = now
        await self.db.flush()
        return events

    async def reap_exhausted_outbox(self) -> int:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(TMemoryOutbox)
            .where(
                TMemoryOutbox.status == "claimed",
                TMemoryOutbox.lease_until < now,
                TMemoryOutbox.attempts >= TMemoryOutbox.max_attempts,
            )
            .values(
                status="dead",
                claim_token=None,
                worker_id=None,
                lease_until=None,
                next_retry_at=None,
                updated_at=now,
            )
        )
        return int(result.rowcount or 0)

    async def finish_outbox(self, *, event_id: str, claim_token: str) -> bool:
        result = await self.db.execute(
            update(TMemoryOutbox)
            .where(
                TMemoryOutbox.id == event_id,
                TMemoryOutbox.status == "claimed",
                TMemoryOutbox.claim_token == claim_token,
            )
            .values(
                status="succeeded",
                claim_token=None,
                worker_id=None,
                lease_until=None,
                next_retry_at=None,
                error_summary=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        return result.rowcount == 1

    async def renew_outbox(
        self, *, event_id: str, claim_token: str, lease_seconds: float
    ) -> bool:
        result = await self.db.execute(
            update(TMemoryOutbox)
            .where(
                TMemoryOutbox.id == event_id,
                TMemoryOutbox.status == "claimed",
                TMemoryOutbox.claim_token == claim_token,
            )
            .values(
                lease_until=datetime.now(timezone.utc)
                + timedelta(seconds=lease_seconds),
                updated_at=datetime.now(timezone.utc),
            )
        )
        return result.rowcount == 1

    async def retry_outbox(
        self,
        *,
        event_id: str,
        claim_token: str,
        error_summary: str,
        retry_seconds: float,
    ) -> bool:
        event = (
            await self.db.execute(
                select(TMemoryOutbox)
                .where(
                    TMemoryOutbox.id == event_id,
                    TMemoryOutbox.status == "claimed",
                    TMemoryOutbox.claim_token == claim_token,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if event is None:
            return False
        event.status = "dead" if event.attempts >= event.max_attempts else "failed"
        event.error_summary = error_summary[:500]
        event.next_retry_at = (
            None
            if event.status == "dead"
            else datetime.now(timezone.utc) + timedelta(seconds=retry_seconds)
        )
        event.claim_token = None
        event.worker_id = None
        event.lease_until = None
        event.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return True

    async def processing_health(self, user_id: str) -> MemoryProcessingHealth:
        job_rows = (
            await self.db.execute(
                select(TMemoryJob.status, func.count(TMemoryJob.id))
                .where(TMemoryJob.user_id == str(user_id))
                .group_by(TMemoryJob.status)
            )
        ).all()
        last_capture = await self.db.scalar(
            select(func.max(TMemoryRunSnapshot.captured_at)).where(
                TMemoryRunSnapshot.user_id == str(user_id)
            )
        )
        last_consolidation = await self.db.scalar(
            select(func.max(TMemoryJob.updated_at)).where(
                TMemoryJob.user_id == str(user_id),
                TMemoryJob.phase == "consolidate",
                TMemoryJob.status.in_(("succeeded", "succeeded_no_output")),
            )
        )
        outbox_rows = (
            await self.db.execute(
                select(
                    TMemoryOutbox.target,
                    TMemoryOutbox.status,
                    func.count(TMemoryOutbox.id),
                    func.min(TMemoryOutbox.created_at),
                )
                .where(
                    TMemoryOutbox.user_id == str(user_id),
                    TMemoryOutbox.status.in_(("pending", "claimed", "failed", "dead")),
                )
                .group_by(TMemoryOutbox.target, TMemoryOutbox.status)
            )
        ).all()
        oldest = min(
            (
                created_at
                for _, status, _, created_at in outbox_rows
                if status != "dead" and created_at is not None
            ),
            default=None,
        )
        return MemoryProcessingHealth(
            job_counts={status: int(count) for status, count in job_rows},
            last_capture_at=last_capture,
            last_consolidation_at=last_consolidation,
            outbox_counts={
                target: sum(
                    int(count)
                    for row_target, status, count, _ in outbox_rows
                    if row_target == target and status != "dead"
                )
                for target in {row[0] for row in outbox_rows}
            },
            outbox_dead_counts={
                target: sum(
                    int(count)
                    for row_target, status, count, _ in outbox_rows
                    if row_target == target and status == "dead"
                )
                for target in {row[0] for row in outbox_rows}
            },
            oldest_outbox_at=oldest,
        )


__all__ = [
    "CaptureContext",
    "CaptureSource",
    "MachineMemoryRepository",
    "MemoryProcessingHealth",
]
