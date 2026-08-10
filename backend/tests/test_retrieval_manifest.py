from concurrent.futures import ThreadPoolExecutor

import pytest

from noesis.runtime.evidence import (
    EvidenceEnvelope,
    EvidenceIdCollisionError,
    RetrievalManifest,
)


def envelope(segment_id: str = "seg-1") -> EvidenceEnvelope:
    return EvidenceEnvelope(
        collection_name="requirements",
        document_id="doc-1",
        document_version_id="docv-1",
        segment_id=segment_id,
        title="登录.md",
        excerpt="验证码五分钟内有效",
        locator={"type": "page", "page_start": 3, "page_end": 3},
        score=0.9,
    )


def test_manifest_reuses_id_across_parallel_tool_calls() -> None:
    manifest = RetrievalManifest(run_salt="fixed-run-salt")

    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(
            pool.map(
                lambda index: manifest.register(
                    envelope(), tool_call_id=f"call-{index}"
                ),
                range(20),
            )
        )

    assert len({entry.evidence_id for entry in entries}) == 1
    stored = manifest.entries()
    assert len(stored) == 1
    assert stored[0].tool_call_ids == sorted(
        [f"call-{index}" for index in range(20)]
    )


def test_manifest_checkpoint_replay_keeps_evidence_id() -> None:
    manifest = RetrievalManifest(run_salt="fixed-run-salt")
    original = manifest.register(envelope(), tool_call_id="call-a")

    restored = RetrievalManifest.from_dict(manifest.to_dict())
    replay = restored.register(envelope(), tool_call_id="call-b")

    assert replay.evidence_id == original.evidence_id
    assert replay.tool_call_ids == ["call-a", "call-b"]


def test_manifest_rejects_id_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = RetrievalManifest(run_salt="fixed-run-salt")
    monkeypatch.setattr(manifest, "_derive_id", lambda _canonical: "ev_collision")
    manifest.register(envelope("seg-1"), tool_call_id="call-a")

    with pytest.raises(EvidenceIdCollisionError):
        manifest.register(envelope("seg-2"), tool_call_id="call-b")


def test_locator_schema_is_strict() -> None:
    with pytest.raises(ValueError):
        EvidenceEnvelope(
            collection_name="requirements",
            document_id="doc-1",
            document_version_id="docv-1",
            segment_id="seg-1",
            title="登录.md",
            excerpt="证据",
            locator={"page_start": 1},
        )
