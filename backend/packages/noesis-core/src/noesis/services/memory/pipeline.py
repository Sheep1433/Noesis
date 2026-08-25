"""Fenced capture and extraction stages for machine-memory jobs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.repositories.machine_memory_repository import MachineMemoryRepository
from noesis.repositories.memory_preference_repository import MemoryPreferenceRepository
from noesis.schemas.memory import MemoryChunk, RunSnapshotPayload, ValidatedMemoryCandidate
from noesis.services.memory.chunking import MemoryChunker
from noesis.services.memory.extractor import MemoryExtractor, merge_candidates
from noesis.services.memory.consolidation import MemoryConsolidationService
from noesis.services.memory.snapshot import RunSnapshotBuilder


class MemoryPipelineProcessor:
    def __init__(
        self,
        extractor: MemoryExtractor,
        *,
        chunker: MemoryChunker | None = None,
        retry_seconds: float = 30,
    ):
        self.extractor = extractor
        self.chunker = chunker or MemoryChunker()
        self.retry_seconds = retry_seconds

    async def process(self, db: AsyncSession, *, job_id: str, claim_token: str) -> str:
        repository = MachineMemoryRepository(db)
        job = await repository.get_claimed_job(job_id=job_id, claim_token=claim_token)
        if job is None:
            return "stale_claim"
        if not await MemoryPreferenceRepository(db).is_enabled(job.user_id):
            await repository.finish_claimed_job(
                job_id=job_id,
                claim_token=claim_token,
                status="skipped_disabled",
                stage_result=job.stage_result or {},
                coverage=job.coverage or {},
            )
            await db.commit()
            return "skipped_disabled"
        try:
            if job.phase == "capture":
                outcome = await self._capture(db, repository, job, claim_token)
            elif job.phase == "extract":
                outcome = await self._extract(db, repository, job, claim_token)
            elif job.phase == "consolidate":
                outcome = await self._consolidate(db, repository, job, claim_token)
            else:
                raise ValueError(f"unsupported memory job phase: {job.phase}")
            await db.commit()
            return outcome
        except Exception as exc:
            await db.rollback()
            job = await repository.get_claimed_job(job_id=job_id, claim_token=claim_token)
            if job is None:
                return "stale_claim"
            await repository.retry_claimed_job(
                job_id=job_id,
                claim_token=claim_token,
                status="failed",
                stage_result=job.stage_result or {},
                coverage=job.coverage or {},
                error_summary=f"{type(exc).__name__}: {exc}",
                retry_seconds=self.retry_seconds,
            )
            await db.commit()
            return "failed"

    async def _capture(self, db, repository, job, claim_token: str) -> str:
        payload = await RunSnapshotBuilder.build(db, job.run_id)
        chunks = self.chunker.chunk(payload)
        chunk_metadata = {"chunks": [chunk.model_dump(mode="json") for chunk in chunks]}
        coverage = {
            "expected_chunk_ids": [chunk.chunk_id for chunk in chunks],
            "processed_chunk_ids": [],
            "failed_chunk_ids": [],
        }
        snapshot = await repository.create_snapshot(
            run_id=payload.run_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            scope_key=payload.scope_key,
            source_watermark=payload.source_watermark,
            schema_version=payload.schema_version,
            content_digest=payload.content_digest,
            evidence_json=payload.model_dump(mode="json"),
            token_estimate=payload.token_estimate,
            chunk_metadata=chunk_metadata,
            coverage=coverage,
        )
        stage_result = {
            "snapshot_digest": payload.content_digest,
            "chunk_candidates": {},
        }
        advanced = await repository.advance_claimed_job(
            job_id=job.id,
            claim_token=claim_token,
            phase="extract",
            stage_result=stage_result,
            coverage=coverage,
            snapshot_id=snapshot.id,
        )
        if not advanced:
            raise RuntimeError("capture claim lost before stage commit")
        await repository.set_snapshot_processing(snapshot.id, status="extracting")
        return "captured"

    async def _extract(self, db, repository, job, claim_token: str) -> str:
        if not job.snapshot_id:
            raise RuntimeError("extract phase is missing snapshot")
        snapshot_row = await repository.get_snapshot(job.snapshot_id)
        if snapshot_row is None or not isinstance(snapshot_row.evidence_json, dict):
            raise RuntimeError("extract snapshot is unavailable")
        payload = RunSnapshotPayload.model_validate(snapshot_row.evidence_json)
        raw_chunks = (
            snapshot_row.chunk_metadata.get("chunks")
            if isinstance(snapshot_row.chunk_metadata, dict)
            else None
        )
        chunks = [MemoryChunk.model_validate(item) for item in (raw_chunks or [])]
        stage_result: dict[str, Any] = dict(job.stage_result or {})
        stored: dict[str, list[dict]] = dict(stage_result.get("chunk_candidates") or {})
        pending = [chunk for chunk in chunks if chunk.chunk_id not in stored]

        if pending:
            result = await self.extractor.extract(payload, pending)
            for chunk_id, candidates in result.chunk_candidates:
                stored[chunk_id] = [candidate.model_dump(mode="json") for candidate in candidates]
            stage_result["chunk_candidates"] = stored
            processed = sorted(stored)
            failed = sorted(set(result.failed_chunk_ids))
            coverage = {
                "expected_chunk_ids": [chunk.chunk_id for chunk in chunks],
                "processed_chunk_ids": processed,
                "failed_chunk_ids": failed,
            }
            if failed:
                status = "partial" if processed else "failed"
                categories = ",".join(
                    sorted({category for _, category in result.failed_chunk_categories})
                )
                await repository.retry_claimed_job(
                    job_id=job.id,
                    claim_token=claim_token,
                    status=status,
                    stage_result=stage_result,
                    coverage=coverage,
                    error_summary=f"{len(failed)} chunk(s) failed: {categories}",
                    retry_seconds=self.retry_seconds,
                )
                await repository.set_snapshot_processing(
                    job.snapshot_id,
                    status=status,
                    error_summary=f"{len(failed)} chunk(s) failed: {categories}",
                )
                return status
        coverage = {
            "expected_chunk_ids": [chunk.chunk_id for chunk in chunks],
            "processed_chunk_ids": sorted(stored),
            "failed_chunk_ids": [],
        }
        candidates = merge_candidates([
            ValidatedMemoryCandidate.model_validate(candidate)
            for items in stored.values()
            for candidate in items
        ])
        stage_result["candidates"] = [candidate.model_dump(mode="json") for candidate in candidates]
        if not candidates:
            await repository.finish_claimed_job(
                job_id=job.id,
                claim_token=claim_token,
                status="succeeded_no_output",
                stage_result=stage_result,
                coverage=coverage,
            )
            await repository.set_snapshot_processing(job.snapshot_id, status="succeeded_no_output")
            return "succeeded_no_output"
        advanced = await repository.advance_claimed_job(
            job_id=job.id,
            claim_token=claim_token,
            phase="consolidate",
            stage_result=stage_result,
            coverage=coverage,
        )
        if not advanced:
            raise RuntimeError("extract claim lost before stage commit")
        await repository.set_snapshot_processing(job.snapshot_id, status="consolidating")
        return "extracted"

    async def _consolidate(self, db, repository, job, claim_token: str) -> str:
        if not job.snapshot_id:
            raise RuntimeError("consolidate phase is missing snapshot")
        snapshot_row = await repository.get_snapshot(job.snapshot_id)
        if snapshot_row is None or not isinstance(snapshot_row.evidence_json, dict):
            raise RuntimeError("consolidation snapshot is unavailable")
        snapshot = RunSnapshotPayload.model_validate(snapshot_row.evidence_json)
        stage_result = dict(job.stage_result or {})
        candidates = [
            ValidatedMemoryCandidate.model_validate(item)
            for item in stage_result.get("candidates") or []
        ]
        outcomes = await MemoryConsolidationService.consolidate(
            db,
            snapshot_id=job.snapshot_id,
            snapshot=snapshot,
            candidates=candidates,
        )
        stage_result["consolidation"] = [
            {
                "operation": outcome.operation,
                "memory_id": outcome.memory_id,
                "status": outcome.status,
            }
            for outcome in outcomes
        ]
        finished = await repository.finish_claimed_job(
            job_id=job.id,
            claim_token=claim_token,
            status="succeeded",
            stage_result=stage_result,
            coverage=job.coverage or {},
        )
        if not finished:
            raise RuntimeError("consolidation claim lost before stage commit")
        await repository.set_snapshot_processing(job.snapshot_id, status="succeeded")
        return "consolidated"


__all__ = ["MemoryPipelineProcessor"]
