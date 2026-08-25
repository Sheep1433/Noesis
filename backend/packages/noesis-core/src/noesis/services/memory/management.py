"""User-scoped machine-memory viewing, revision, state, deletion and health."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.memory_paths import scope_digest
from noesis.errors.exceptions import ConflictException, NotFoundException
from noesis.repositories.machine_memory_repository import MachineMemoryRepository
from noesis.schemas.memory import (
    MemoryEvidenceSummary,
    MemoryItemResponse,
    MemoryProcessingHealthResponse,
    MemoryStateResponse,
)
from noesis.storage.postgres.models.memory import (
    TMemoryEvidence,
    TMemoryItem,
    TMemoryRelation,
)


def _scope_label(scope_key: str) -> str:
    return (
        "非项目任务"
        if scope_key.endswith("project:global")
        else f"项目 {scope_digest(scope_key)[:8]}"
    )


def _revision_digest(statement: str, applicability: str) -> str:
    value = json.dumps(
        {"statement": statement, "applicability": applicability},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MachineMemoryService:
    @staticmethod
    def _response(
        item: TMemoryItem,
        evidence: list[TMemoryEvidence],
        evidence_run_count: int,
    ) -> MemoryItemResponse:
        return MemoryItemResponse(
            id=item.id,
            memory_type=item.memory_type,
            status=item.status,
            subject=item.subject,
            statement=item.statement,
            applicability=item.applicability,
            scope_id=scope_digest(item.scope_key),
            scope_label=_scope_label(item.scope_key),
            effective_provenance=item.effective_provenance,
            version=item.version,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            last_verified_at=item.last_verified_at,
            user_revision=item.user_revision,
            evidence_count=evidence_run_count,
            evidence=[
                MemoryEvidenceSummary(
                    id=value.id,
                    source_kind=value.source_kind,
                    provenance=value.provenance,
                    created_at=value.created_at,
                )
                for value in evidence
            ],
        )

    @classmethod
    async def list_items(
        cls,
        db: AsyncSession,
        *,
        user_id: str,
        statuses: tuple[str, ...] = (),
        memory_types: tuple[str, ...] = (),
        scope_id: str = "",
        query: str = "",
        limit: int = 100,
    ) -> list[MemoryItemResponse]:
        repository = MachineMemoryRepository(db)
        items = await repository.list_user_items(
            user_id=str(user_id),
            statuses=statuses,
            memory_types=memory_types,
            query=query,
            limit=200 if scope_id else limit,
        )
        if scope_id:
            items = [
                item for item in items if scope_digest(item.scope_key) == scope_id
            ][:limit]
        evidence_by_item = await repository.list_evidence_by_items(
            user_id=str(user_id), memory_ids=[item.id for item in items]
        )
        evidence_run_counts = await repository.count_evidence_runs(
            user_id=str(user_id), memory_ids=[item.id for item in items]
        )
        return [
            cls._response(
                item,
                evidence_by_item.get(item.id, []),
                evidence_run_counts.get(item.id, 0),
            )
            for item in items
        ]

    @classmethod
    async def get_item(
        cls, db: AsyncSession, *, user_id: str, memory_id: str
    ) -> MemoryItemResponse:
        repository = MachineMemoryRepository(db)
        item = await repository.get_item(memory_id, user_id=str(user_id))
        if item is None:
            raise NotFoundException(message="记忆不存在")
        evidence = await repository.list_item_evidence(
            user_id=str(user_id), memory_id=item.id
        )
        evidence_run_counts = await repository.count_evidence_runs(
            user_id=str(user_id), memory_ids=[item.id]
        )
        return cls._response(item, evidence, evidence_run_counts.get(item.id, 0))

    @classmethod
    async def revise(
        cls,
        db: AsyncSession,
        *,
        user_id: str,
        memory_id: str,
        statement: str,
        applicability: str,
    ) -> MemoryItemResponse:
        repository = MachineMemoryRepository(db)
        current = await repository.get_item_for_update(
            user_id=str(user_id), memory_id=memory_id
        )
        if current is None:
            raise NotFoundException(message="记忆不存在")
        if current.status == "superseded":
            raise ConflictException(message="历史版本不能修改")
        digest = _revision_digest(statement, applicability)
        if (
            current.user_revision
            and current.statement == statement
            and current.applicability == applicability
        ):
            evidence = await repository.list_item_evidence(
                user_id=str(user_id), memory_id=current.id
            )
            evidence_run_counts = await repository.count_evidence_runs(
                user_id=str(user_id), memory_ids=[current.id]
            )
            return cls._response(
                current, evidence, evidence_run_counts.get(current.id, 0)
            )
        now = datetime.now(timezone.utc)
        current.status = "superseded"
        current.valid_to = now
        repository.add_desired_state_events(current)
        await db.flush()
        revised = TMemoryItem(
            user_id=str(user_id),
            scope_key=current.scope_key,
            memory_type=current.memory_type,
            subject=current.subject,
            subject_key=current.subject_key,
            statement=statement,
            applicability=applicability,
            content_digest=digest,
            effective_provenance="user",
            status="active",
            version=current.version + 1,
            last_verified_at=now,
            supersedes_id=current.id,
            user_revision=True,
        )
        repository.add_item(revised)
        await db.flush()
        repository.add_relation(
            TMemoryRelation(
                user_id=str(user_id),
                source_item_id=revised.id,
                target_item_id=current.id,
                relation_type="supersedes",
            )
        )
        repository.add_desired_state_events(revised)
        db.add(
            TMemoryEvidence(
                memory_id=revised.id,
                snapshot_id=None,
                user_id=str(user_id),
                run_id=None,
                source_kind="user_revision",
                source_ref=f"user_revision:{revised.id}",
                span_digest=digest,
                provenance="user",
                excerpt=statement,
            )
        )
        await db.commit()
        evidence = await repository.list_item_evidence(
            user_id=str(user_id), memory_id=revised.id
        )
        evidence_run_counts = await repository.count_evidence_runs(
            user_id=str(user_id), memory_ids=[revised.id]
        )
        return cls._response(revised, evidence, evidence_run_counts.get(revised.id, 0))

    @classmethod
    async def change_state(
        cls,
        db: AsyncSession,
        *,
        user_id: str,
        memory_id: str,
        operation: str,
    ) -> MemoryStateResponse:
        repository = MachineMemoryRepository(db)
        item = await repository.get_item_for_update(
            user_id=str(user_id), memory_id=memory_id
        )
        if item is None:
            raise NotFoundException(message="记忆不存在")
        transitions = {
            "activate": ({"candidate"}, "active"),
            "disable": ({"candidate", "active", "needs_review"}, "disabled"),
            "enable": ({"disabled"}, "active"),
            "invalidate": (
                {"candidate", "active", "needs_review", "disabled"},
                "invalidated",
            ),
        }
        if operation not in transitions:
            raise ConflictException(message="不支持该操作")
        allowed, target = transitions[operation]
        if item.status == target:
            return MemoryStateResponse(id=item.id, status=item.status)
        if item.status not in allowed:
            raise ConflictException(message="当前状态不能执行此操作")
        item.status = target
        if target == "active":
            item.valid_to = None
            item.last_verified_at = datetime.now(timezone.utc)
            item.user_revision = True
            item.effective_provenance = "user"
        repository.add_desired_state_events(item)
        await db.commit()
        return MemoryStateResponse(id=item.id, status=item.status)

    @staticmethod
    async def delete(db: AsyncSession, *, user_id: str, memory_id: str) -> None:
        repository = MachineMemoryRepository(db)
        item = await repository.get_item_for_update(
            user_id=str(user_id), memory_id=memory_id
        )
        if item is None:
            raise NotFoundException(message="记忆不存在")
        await repository.delete_item(item)
        await db.commit()

    @staticmethod
    async def health(
        db: AsyncSession, *, user_id: str
    ) -> MemoryProcessingHealthResponse:
        health = await MachineMemoryRepository(db).processing_health(str(user_id))
        lag = None
        if health.oldest_outbox_at is not None:
            lag = max(
                0,
                int(
                    (
                        datetime.now(timezone.utc) - health.oldest_outbox_at
                    ).total_seconds()
                ),
            )
        return MemoryProcessingHealthResponse(
            last_capture_at=health.last_capture_at,
            last_consolidation_at=health.last_consolidation_at,
            pending=health.job_counts.get("pending", 0)
            + health.job_counts.get("claimed", 0),
            partial=health.job_counts.get("partial", 0),
            failed=health.job_counts.get("failed", 0),
            dead=health.job_counts.get("dead", 0),
            skipped=health.job_counts.get("skipped_disabled", 0),
            workspace_pending=health.outbox_counts.get("workspace", 0),
            index_pending=health.outbox_counts.get("index", 0),
            workspace_failed=health.outbox_dead_counts.get("workspace", 0),
            index_failed=health.outbox_dead_counts.get("index", 0),
            derived_view_lag_seconds=lag,
        )


__all__ = ["MachineMemoryService"]
