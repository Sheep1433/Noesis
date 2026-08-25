"""Structure-preserving, token-aware chunking for Run snapshots."""

from __future__ import annotations

import hashlib
import json

from noesis.schemas.memory import MemoryChunk, MemorySourceSpan, RunSnapshotPayload


def estimate_tokens(value: str) -> int:
    return (len(value) + 3) // 4


def _render_span(span: MemorySourceSpan) -> str:
    return json.dumps(
        {
            "id": span.id,
            "kind": span.kind,
            "provenance": span.effective_provenance,
            "text": span.text,
            "metadata": span.metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class MemoryChunker:
    def __init__(self, *, max_tokens: int = 1_600):
        if max_tokens < 1_200:
            raise ValueError("memory chunk budget must be at least 1200 tokens")
        self.max_tokens = max_tokens

    def chunk(self, snapshot: RunSnapshotPayload) -> list[MemoryChunk]:
        chunks: list[MemoryChunk] = []
        current: list[tuple[MemorySourceSpan, str]] = []
        current_tokens = 0

        def flush() -> None:
            nonlocal current, current_tokens
            if not current:
                return
            ordinal = len(chunks)
            text = "\n".join(rendered for _, rendered in current)
            span_ids = [span.id for span, _ in current]
            material = json.dumps(
                {
                    "snapshot": snapshot.content_digest,
                    "ordinal": ordinal,
                    "span_ids": span_ids,
                    "text": text,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            chunks.append(MemoryChunk(
                chunk_id=hashlib.sha256(material.encode("utf-8")).hexdigest(),
                ordinal=ordinal,
                span_ids=span_ids,
                token_estimate=estimate_tokens(text),
                text=text,
            ))
            current = []
            current_tokens = 0

        for span in snapshot.spans:
            rendered = _render_span(span)
            tokens = estimate_tokens(rendered)
            if tokens > self.max_tokens:
                raise ValueError(f"bounded source span exceeds chunk budget: {span.id}")
            if current and current_tokens + tokens > self.max_tokens:
                flush()
            current.append((span, rendered))
            current_tokens += tokens
        flush()
        return chunks


__all__ = ["MemoryChunker", "estimate_tokens"]
