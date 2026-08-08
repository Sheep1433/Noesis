from noesis.knowledge.retrieval.payload import (
    build_evidence_identity,
    build_payload,
    build_typed_locator,
)


def test_evidence_identity_is_stable_and_versioned() -> None:
    first = build_evidence_identity(
        collection_name="requirements",
        file_name="登录.md",
        file_hash="file-v1",
        chunk_index=2,
        content_hash="chunk-a",
    )
    replay = build_evidence_identity(
        collection_name="requirements",
        file_name="登录.md",
        file_hash="file-v1",
        chunk_index=2,
        content_hash="chunk-a",
    )
    new_version = build_evidence_identity(
        collection_name="requirements",
        file_name="登录.md",
        file_hash="file-v2",
        chunk_index=2,
        content_hash="chunk-a",
    )

    assert replay == first
    assert new_version["document_id"] == first["document_id"]
    assert new_version["document_version_id"] != first["document_version_id"]
    assert new_version["segment_id"] != first["segment_id"]


def test_payload_persists_identity_at_top_level_and_metadata() -> None:
    payload = build_payload(
        page_content="验证码五分钟内有效",
        metadata={
            "file_name": "登录.md",
            "chunk_index": 0,
            "page_no": 3,
        },
        collection_name="requirements",
        file_hash="file-v1",
    )

    for key in ("document_id", "document_version_id", "segment_id"):
        assert payload[key]
        assert payload["metadata"][key] == payload[key]
    assert payload["locator"] == {
        "type": "page",
        "page_start": 3,
        "page_end": 3,
    }


def test_locator_falls_back_to_header_and_rejects_untyped_shape() -> None:
    assert build_typed_locator(
        {"locator": {"page_start": 1}, "header_path": "登录 > 验证码"}
    ) == {"type": "header", "path": ["登录", "验证码"]}
    assert build_typed_locator({}) is None
