"""Four-type structured extraction with evidence validation and deterministic merge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from noesis.schemas.memory import (
    MemoryCandidate,
    MemoryChunk,
    MemorySourceSpan,
    RunSnapshotPayload,
    ValidatedMemoryCandidate,
)


CandidateModel = Callable[[MemoryChunk], Awaitable[list[dict]]]
_TRUST = {"tool_external": 0, "assistant_derived": 1, "tool_internal": 2, "user": 3}
_WORKFLOW_STOP_RE = re.compile(r"\b(?:stop|abort|halt)\b|停止|终止", re.I)
_WORKFLOW_SEQUENCE_RE = re.compile(
    r"\b(?:first|then|next|after|before|procedure|step)\b|先|然后|随后|再|步骤",
    re.I,
)


def _needs_high_value_retry(
    chunk: MemoryChunk, spans: dict[str, MemorySourceSpan]
) -> bool:
    kinds = {spans[ref].kind for ref in chunk.span_ids if ref in spans}
    return bool(kinds & {"user_goal", "user_correction"} and "validation" in kinds)


def canonical_subject_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", normalized)
    normalized = " ".join(normalized.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _content_digest(candidate: MemoryCandidate) -> str:
    payload = candidate.model_dump(mode="json", exclude={"evidence_refs", "confidence_reason"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_candidate(
    raw: dict,
    *,
    chunk: MemoryChunk,
    spans: dict[str, MemorySourceSpan],
) -> ValidatedMemoryCandidate:
    candidate = MemoryCandidate.model_validate(raw)
    refs = set(candidate.evidence_refs)
    if not refs <= set(chunk.span_ids) or not refs <= spans.keys():
        raise ValueError("candidate references evidence outside the current chunk")
    evidence_kinds = {spans[ref].kind for ref in refs}
    if (
        candidate.memory_type == "decision"
        and evidence_kinds & {"user_goal", "user_correction"}
        and "validation" in evidence_kinds
        and _WORKFLOW_STOP_RE.search(candidate.statement)
        and _WORKFLOW_SEQUENCE_RE.search(candidate.statement)
    ):
        candidate = candidate.model_copy(update={"memory_type": "workflow"})
    effective = min(
        (spans[ref].effective_provenance for ref in refs),
        key=lambda provenance: _TRUST[provenance],
    )
    return ValidatedMemoryCandidate(
        **candidate.model_dump(),
        subject_key=canonical_subject_key(candidate.subject),
        effective_provenance=effective,
        content_digest=_content_digest(candidate),
        chunk_ids=[chunk.chunk_id],
    )


def merge_candidates(candidates: list[ValidatedMemoryCandidate]) -> list[ValidatedMemoryCandidate]:
    grouped: dict[tuple[str, str], list[ValidatedMemoryCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault((candidate.memory_type, candidate.subject_key), []).append(candidate)
    merged: list[ValidatedMemoryCandidate] = []
    for key in sorted(grouped):
        group = grouped[key]
        representative = max(group, key=lambda item: (len(item.statement), item.statement, item.content_digest))
        applicability = max(
            (item.applicability for item in group), key=lambda value: (len(value), value)
        )
        evidence_refs = sorted({ref for item in group for ref in item.evidence_refs})
        chunk_ids = sorted({chunk_id for item in group for chunk_id in item.chunk_ids})
        effective = min(
            (item.effective_provenance for item in group), key=lambda value: _TRUST[value]
        )
        material = {
            "memory_type": representative.memory_type,
            "subject_key": representative.subject_key,
            "statement": representative.statement,
            "applicability": applicability,
        }
        digest = hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        merged.append(representative.model_copy(update={
            "applicability": applicability,
            "evidence_refs": evidence_refs,
            "effective_provenance": effective,
            "content_digest": digest,
            "chunk_ids": chunk_ids,
        }))
    return merged


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    candidates: tuple[ValidatedMemoryCandidate, ...]
    processed_chunk_ids: tuple[str, ...]
    failed_chunk_ids: tuple[str, ...]
    failed_chunk_categories: tuple[tuple[str, str], ...]
    chunk_candidates: tuple[tuple[str, tuple[ValidatedMemoryCandidate, ...]], ...]


class MemoryExtractor:
    def __init__(
        self,
        model: CandidateModel,
        *,
        concurrency: int = 4,
        chunk_attempts: int = 2,
        retry_delay_seconds: float = 0.5,
    ):
        self.model = model
        self.concurrency = max(1, concurrency)
        self.chunk_attempts = max(1, chunk_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)

    async def extract(
        self, snapshot: RunSnapshotPayload, chunks: list[MemoryChunk]
    ) -> ExtractionResult:
        spans = {span.id: span for span in snapshot.spans}
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_chunk(chunk: MemoryChunk):
            async with semaphore:
                last_error: Exception | None = None
                for attempt in range(self.chunk_attempts):
                    try:
                        raw_candidates = await self.model(chunk)
                        if not raw_candidates and _needs_high_value_retry(chunk, spans):
                            retry = getattr(self.model, "retry_high_value", None)
                            if retry is not None:
                                raw_candidates = await retry(chunk)
                        return chunk, [
                            validate_candidate(raw, chunk=chunk, spans=spans)
                            for raw in raw_candidates
                        ], None
                    except Exception as exc:  # chunk retry boundary records the final gap
                        last_error = exc
                        if attempt + 1 < self.chunk_attempts and self.retry_delay_seconds:
                            await asyncio.sleep(
                                self.retry_delay_seconds * (2**attempt)
                            )
                return chunk, [], last_error

        results = await asyncio.gather(*(run_chunk(chunk) for chunk in chunks))
        processed = [chunk.chunk_id for chunk, _, error in results if error is None]
        failed = [chunk.chunk_id for chunk, _, error in results if error is not None]
        failed_categories = [
            (chunk.chunk_id, type(error).__name__)
            for chunk, _, error in results
            if error is not None
        ]
        candidates = merge_candidates([
            candidate for _, items, error in results if error is None for candidate in items
        ])
        if failed and not processed:
            status = "failed"
        elif failed:
            status = "partial"
        elif candidates:
            status = "succeeded"
        else:
            status = "succeeded_no_output"
        chunk_candidates = tuple(
            (chunk.chunk_id, tuple(items))
            for chunk, items, error in results
            if error is None
        )
        return ExtractionResult(
            status=status,
            candidates=tuple(candidates),
            processed_chunk_ids=tuple(processed),
            failed_chunk_ids=tuple(failed),
            failed_chunk_categories=tuple(failed_categories),
            chunk_candidates=chunk_candidates,
        )


__all__ = [
    "ExtractionResult",
    "MemoryExtractor",
    "canonical_subject_key",
    "merge_candidates",
    "validate_candidate",
]
