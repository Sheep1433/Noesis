"""User-scoped source lookup and account-level machine-memory cleanup."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.errors.exceptions import NotFoundException
from noesis.repositories.machine_memory_repository import MachineMemoryRepository
from noesis.schemas.memory import MemorySourceResponse, RunSnapshotPayload
from noesis.services.memory.workspace import MemoryWorkspaceService
from noesis.services.memory.index import MemoryIndexService
from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage


class MemorySourceService:
    @staticmethod
    async def get(
        db: AsyncSession,
        *,
        user_id: str,
        memory_id: str,
        evidence_id: str,
        scope_key: str | None = None,
    ) -> MemorySourceResponse:
        repository = MachineMemoryRepository(db)
        pair = await repository.get_evidence_for_user(
            user_id=str(user_id),
            memory_id=memory_id,
            evidence_id=evidence_id,
            scope_key=scope_key,
        )
        if pair is None:
            raise NotFoundException(message="记忆来源不存在")
        _, evidence = pair
        if not evidence.snapshot_id:
            return MemorySourceResponse(
                memory_id=memory_id,
                evidence_id=evidence_id,
                availability="retention_expired",
                source_kind=evidence.source_kind,
            )
        snapshot = await repository.get_snapshot(evidence.snapshot_id)
        if snapshot is None or not isinstance(snapshot.evidence_json, dict):
            return MemorySourceResponse(
                memory_id=memory_id,
                evidence_id=evidence_id,
                availability="retention_expired",
                source_kind=evidence.source_kind,
            )
        run = await db.get(TAgentRun, snapshot.run_id)
        assistant = await db.get(TChatMessage, run.assistant_message_id) if run else None
        if run is None or assistant is None or assistant.deleted_at is not None:
            return MemorySourceResponse(
                memory_id=memory_id,
                evidence_id=evidence_id,
                availability="source_deleted",
                source_kind=evidence.source_kind,
            )
        if evidence.source_kind == "message" and evidence.source_ref.startswith("message:"):
            message_id = evidence.source_ref.split(":", 1)[1].split("#", 1)[0]
            message = await db.get(TChatMessage, message_id)
            if message is None or message.deleted_at is not None:
                return MemorySourceResponse(
                    memory_id=memory_id,
                    evidence_id=evidence_id,
                    availability="source_deleted",
                    source_kind=evidence.source_kind,
                )
        payload = RunSnapshotPayload.model_validate(snapshot.evidence_json)
        span = next(
            (
                item
                for item in payload.spans
                if item.source_ref == evidence.source_ref and item.digest == evidence.span_digest
            ),
            None,
        )
        if span is None:
            return MemorySourceResponse(
                memory_id=memory_id,
                evidence_id=evidence_id,
                availability="retention_expired",
                source_kind=evidence.source_kind,
            )
        return MemorySourceResponse(
            memory_id=memory_id,
            evidence_id=evidence_id,
            availability="available",
            source_kind=evidence.source_kind,
            source_ref=span.source_ref,
            excerpt=span.text,
            provenance=span.effective_provenance,
            source_digest=span.digest,
            role=(
                "user"
                if span.kind in {"user_goal", "user_correction"}
                else "tool"
                if span.kind in {"tool_outcome", "artifact", "validation"}
                else "assistant"
            ),
            tool_outcome=(
                span.metadata
                if span.kind in {"tool_outcome", "artifact", "validation"}
                else None
            ),
            captured_at=snapshot.captured_at,
        )

    @staticmethod
    async def delete_derived_user_data(*, user_id: str) -> None:
        await MemoryIndexService().delete_user(str(user_id))
        MemoryWorkspaceService.remove_user_workspace(str(user_id))

    @staticmethod
    async def delete_user_data(db: AsyncSession, *, user_id: str) -> None:
        await MemorySourceService.delete_derived_user_data(user_id=str(user_id))
        await MachineMemoryRepository(db).delete_user_data(str(user_id))
        await db.commit()


__all__ = ["MemorySourceService"]
