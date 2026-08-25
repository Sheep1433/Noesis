from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from noesis.schemas.memory import MemorySourceSpan, RunSnapshotPayload, ValidatedMemoryCandidate
from noesis.services.memory.chunking import MemoryChunker
from noesis.services.memory.extractor import MemoryExtractor
from noesis.services.memory.pipeline import MemoryPipelineProcessor


def _snapshot_and_chunks():
    spans = [
        MemorySourceSpan(
            id=f"span-{index}",
            source_ref=f"message:{index}",
            kind="assistant_conclusion",
            provenance="assistant_derived",
            effective_provenance="assistant_derived",
            text=character * 3_000,
            digest=character * 64,
        )
        for index, character in enumerate(("a", "b"))
    ]
    snapshot = RunSnapshotPayload(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        scope_key="profile:SUPER_AGENT_QA|project:global",
        source_watermark=1,
        spans=spans,
        content_digest="c" * 64,
        token_estimate=2_000,
    )
    return snapshot, MemoryChunker(max_tokens=1_200).chunk(snapshot)


def _candidate(chunk_id: str, span_id: str, subject: str) -> ValidatedMemoryCandidate:
    return ValidatedMemoryCandidate(
        memory_type="decision",
        subject=subject,
        subject_key=("1" if subject == "First" else "2") * 64,
        statement=f"Keep {subject.casefold()} result.",
        evidence_refs=[span_id],
        effective_provenance="assistant_derived",
        confidence_reason="Evidence is present.",
        content_digest=("3" if subject == "First" else "4") * 64,
        chunk_ids=[chunk_id],
    )


@pytest.mark.asyncio
async def test_extract_stage_reuses_persisted_chunk_results(monkeypatch) -> None:
    snapshot, chunks = _snapshot_and_chunks()
    first = _candidate(chunks[0].chunk_id, "span-0", "First")
    job = SimpleNamespace(
        id="job-1",
        run_id="run-1",
        user_id="user-1",
        phase="extract",
        snapshot_id="snapshot-1",
        stage_result={"chunk_candidates": {chunks[0].chunk_id: [first.model_dump(mode="json")]}},
        coverage={},
    )
    advance = AsyncMock(return_value=True)
    repository = SimpleNamespace(
        get_claimed_job=AsyncMock(return_value=job),
        get_snapshot=AsyncMock(return_value=SimpleNamespace(
            evidence_json=snapshot.model_dump(mode="json"),
            chunk_metadata={"chunks": [chunk.model_dump(mode="json") for chunk in chunks]},
        )),
        advance_claimed_job=advance,
        set_snapshot_processing=AsyncMock(return_value=True),
        retry_claimed_job=AsyncMock(),
        finish_claimed_job=AsyncMock(),
    )
    monkeypatch.setattr(
        "noesis.services.memory.pipeline.MachineMemoryRepository", lambda _db: repository
    )

    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            return True

    monkeypatch.setattr("noesis.services.memory.pipeline.MemoryPreferenceRepository", Preference)
    called: list[str] = []

    async def model(chunk):
        called.append(chunk.chunk_id)
        return [{
            "memory_type": "decision",
            "subject": "Second",
            "statement": "Keep second result.",
            "evidence_refs": ["span-1"],
            "confidence_reason": "Evidence is present.",
        }]

    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    outcome = await MemoryPipelineProcessor(
        MemoryExtractor(model), chunker=MemoryChunker(max_tokens=1_200)
    ).process(db, job_id="job-1", claim_token="token-1")

    assert outcome == "extracted"
    assert called == [chunks[1].chunk_id]
    stage_result = advance.await_args.kwargs["stage_result"]
    assert len(stage_result["chunk_candidates"]) == 2
    assert len(stage_result["candidates"]) == 2
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_preference_stops_at_stage_boundary(monkeypatch) -> None:
    job = SimpleNamespace(
        id="job-1",
        user_id="user-1",
        phase="extract",
        stage_result={},
        coverage={},
    )
    finish = AsyncMock(return_value=True)
    repository = SimpleNamespace(
        get_claimed_job=AsyncMock(return_value=job),
        finish_claimed_job=finish,
    )
    monkeypatch.setattr(
        "noesis.services.memory.pipeline.MachineMemoryRepository", lambda _db: repository
    )

    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            return False

    monkeypatch.setattr("noesis.services.memory.pipeline.MemoryPreferenceRepository", Preference)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    outcome = await MemoryPipelineProcessor(MemoryExtractor(AsyncMock())).process(
        db, job_id="job-1", claim_token="token-1"
    )

    assert outcome == "skipped_disabled"
    assert finish.await_args.kwargs["status"] == "skipped_disabled"
    db.commit.assert_awaited_once()
