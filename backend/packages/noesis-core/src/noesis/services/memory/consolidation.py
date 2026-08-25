"""Evidence-backed deterministic consolidation and version state transitions."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.repositories.machine_memory_repository import MachineMemoryRepository
from noesis.schemas.memory import RunSnapshotPayload, ValidatedMemoryCandidate
from noesis.storage.postgres.models.memory import TMemoryEvidence, TMemoryItem, TMemoryRelation


_SOURCE_KIND = {
    "user_goal": "message",
    "user_correction": "message",
    "assistant_conclusion": "message",
    "tool_outcome": "tool",
    "artifact": "artifact",
    "validation": "artifact",
    "compaction": "chunk",
}


def identity_lock_key(*, user_id: str, scope_key: str, memory_type: str, subject_key: str) -> int:
    material = f"{user_id}\0{scope_key}\0{memory_type}\0{subject_key}"
    unsigned = int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")
    return unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned


def _comparable_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", normalized)
    return " ".join(normalized.split())


def initial_status(candidate: ValidatedMemoryCandidate, snapshot: RunSnapshotPayload) -> str:
    if snapshot.scope_key.endswith("project:global"):
        return "candidate"
    if candidate.effective_provenance == "tool_external":
        return "candidate"
    spans = {span.id: span for span in snapshot.spans}
    kinds = {spans[ref].kind for ref in candidate.evidence_refs if ref in spans}
    provenances = {
        spans[ref].effective_provenance for ref in candidate.evidence_refs if ref in spans
    }
    if candidate.memory_type == "decision":
        return "active" if "user" in provenances or "validation" in kinds else "candidate"
    if candidate.memory_type == "workflow":
        return (
            "active"
            if "validation" in kinds and "user" in provenances
            else "candidate"
        )
    if candidate.memory_type == "gotcha":
        has_failure_outcome = any(
            span.kind == "tool_outcome"
            and (
                span.metadata.get("state") in {"error", "failed", "failure", "timeout"}
                or bool(span.metadata.get("timed_out"))
                or bool(span.metadata.get("error_category"))
                or (
                    isinstance(span.metadata.get("exit_code"), int)
                    and span.metadata["exit_code"] != 0
                )
            )
            for ref in candidate.evidence_refs
            if (span := spans.get(ref)) is not None
        )
        return (
            "active"
            if kinds & {"user_correction", "validation"} or has_failure_outcome
            else "candidate"
        )
    return "active" if kinds & {"tool_outcome", "artifact", "validation"} else "candidate"


def _has_independent_evidence(
    candidate: ValidatedMemoryCandidate, snapshot: RunSnapshotPayload
) -> bool:
    spans = {span.id: span for span in snapshot.spans}
    return any(
        spans[ref].kind != "compaction"
        for ref in candidate.evidence_refs
        if ref in spans
    )


def decide_operation(
    current: TMemoryItem | None,
    candidate: ValidatedMemoryCandidate,
    snapshot: RunSnapshotPayload,
) -> str:
    if current is None:
        return "ADD"
    if current.status in {"disabled", "invalidated"}:
        return "NOOP"
    if current.content_digest == candidate.content_digest:
        if current.id in snapshot.recalled_memory_ids and not _has_independent_evidence(
            candidate, snapshot
        ):
            return "NOOP"
        return "REINFORCE"
    spans = {span.id: span for span in snapshot.spans}
    has_user_correction = any(
        spans[ref].kind == "user_correction" for ref in candidate.evidence_refs if ref in spans
    )
    if current.user_revision and not has_user_correction:
        return "NOOP"
    if candidate.proposed_relation == "contradicts":
        return "CONTRADICT"
    if has_user_correction or candidate.proposed_relation == "supersedes":
        return "SUPERSEDE"
    current_text = _comparable_text(current.statement)
    candidate_text = _comparable_text(candidate.statement)
    if current_text in candidate_text or candidate_text in current_text:
        return "UPDATE"
    return "CONTRADICT"


@dataclass(frozen=True)
class ConsolidationOutcome:
    operation: str
    memory_id: str
    status: str


class MemoryConsolidationService:
    @classmethod
    async def consolidate(
        cls,
        db: AsyncSession,
        *,
        snapshot_id: str,
        snapshot: RunSnapshotPayload,
        candidates: list[ValidatedMemoryCandidate],
    ) -> list[ConsolidationOutcome]:
        repository = MachineMemoryRepository(db)
        outcomes: list[ConsolidationOutcome] = []
        for candidate in sorted(
            candidates, key=lambda item: (item.memory_type, item.subject_key, item.content_digest)
        ):
            lock_key = identity_lock_key(
                user_id=snapshot.user_id,
                scope_key=snapshot.scope_key,
                memory_type=candidate.memory_type,
                subject_key=candidate.subject_key,
            )
            await repository.lock_identity(lock_key)
            current = await repository.get_current_item(
                user_id=snapshot.user_id,
                scope_key=snapshot.scope_key,
                memory_type=candidate.memory_type,
                subject_key=candidate.subject_key,
                for_update=True,
            )
            if current is None:
                neighbors = await repository.bounded_neighbors(
                    user_id=snapshot.user_id,
                    scope_key=snapshot.scope_key,
                    memory_type=candidate.memory_type,
                    subject=candidate.subject,
                    subject_key=candidate.subject_key,
                )
                duplicate = next(
                    (
                        item
                        for item in neighbors
                        if item.content_digest == candidate.content_digest
                    ),
                    None,
                )
                if duplicate is not None:
                    outcomes.append(
                        ConsolidationOutcome("NOOP", duplicate.id, duplicate.status)
                    )
                    continue
            operation = decide_operation(current, candidate, snapshot)
            item = await cls._apply(
                db,
                repository=repository,
                current=current,
                candidate=candidate,
                snapshot_id=snapshot_id,
                snapshot=snapshot,
                operation=operation,
            )
            outcomes.append(ConsolidationOutcome(operation, item.id, item.status))
        return outcomes

    @classmethod
    async def _apply(
        cls,
        db: AsyncSession,
        *,
        repository: MachineMemoryRepository,
        current: TMemoryItem | None,
        candidate: ValidatedMemoryCandidate,
        snapshot_id: str,
        snapshot: RunSnapshotPayload,
        operation: str,
    ) -> TMemoryItem:
        now = datetime.now(timezone.utc)
        if operation in {"REINFORCE", "NOOP"} and current is not None:
            if operation == "REINFORCE":
                current.last_verified_at = now
                await cls._add_evidence(repository, current, candidate, snapshot_id, snapshot)
                repository.add_desired_state_events(current)
            return current

        if operation == "CONTRADICT" and current is not None:
            current.status = "needs_review"
            conflict = cls._new_item(
                candidate,
                snapshot,
                status="superseded",
                version=current.version + 1,
                valid_to=now,
            )
            repository.add_item(conflict)
            await db.flush()
            await cls._add_evidence(repository, conflict, candidate, snapshot_id, snapshot)
            repository.add_relation(TMemoryRelation(
                user_id=snapshot.user_id,
                source_item_id=conflict.id,
                target_item_id=current.id,
                relation_type="contradicts",
            ))
            repository.add_desired_state_events(current)
            repository.add_desired_state_events(conflict)
            return current

        if current is not None:
            current.status = "superseded"
            current.valid_to = now
            repository.add_desired_state_events(current)
            await db.flush()
        status = initial_status(candidate, snapshot)
        item = cls._new_item(
            candidate,
            snapshot,
            status=status,
            version=(current.version + 1 if current is not None else 1),
            supersedes_id=(current.id if current is not None else None),
        )
        repository.add_item(item)
        await db.flush()
        await cls._add_evidence(repository, item, candidate, snapshot_id, snapshot)
        if current is not None:
            repository.add_relation(TMemoryRelation(
                user_id=snapshot.user_id,
                source_item_id=item.id,
                target_item_id=current.id,
                relation_type="supersedes",
            ))
        repository.add_desired_state_events(item)
        return item

    @staticmethod
    def _new_item(
        candidate: ValidatedMemoryCandidate,
        snapshot: RunSnapshotPayload,
        *,
        status: str,
        version: int,
        supersedes_id: str | None = None,
        valid_to: datetime | None = None,
    ) -> TMemoryItem:
        return TMemoryItem(
            user_id=snapshot.user_id,
            scope_key=snapshot.scope_key,
            memory_type=candidate.memory_type,
            subject=candidate.subject,
            subject_key=candidate.subject_key,
            statement=candidate.statement,
            applicability=candidate.applicability,
            content_digest=candidate.content_digest,
            effective_provenance=candidate.effective_provenance,
            status=status,
            version=version,
            valid_to=valid_to,
            last_verified_at=datetime.now(timezone.utc) if status == "active" else None,
            supersedes_id=supersedes_id,
        )

    @staticmethod
    async def _add_evidence(
        repository: MachineMemoryRepository,
        item: TMemoryItem,
        candidate: ValidatedMemoryCandidate,
        snapshot_id: str,
        snapshot: RunSnapshotPayload,
    ) -> None:
        spans = {span.id: span for span in snapshot.spans}
        for ref in candidate.evidence_refs:
            span = spans[ref]
            await repository.add_evidence_if_missing(TMemoryEvidence(
                memory_id=item.id,
                snapshot_id=snapshot_id,
                user_id=snapshot.user_id,
                run_id=snapshot.run_id,
                source_kind=_SOURCE_KIND[span.kind],
                source_ref=span.source_ref,
                span_digest=span.digest,
                provenance=span.effective_provenance,
                excerpt=span.text,
            ))


__all__ = [
    "ConsolidationOutcome",
    "MemoryConsolidationService",
    "decide_operation",
    "identity_lock_key",
    "initial_status",
]
