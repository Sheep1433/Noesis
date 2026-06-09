"""知识库入库/检索参数合并单测（rag-chunking-pipeline）。"""
from kb.chunk import (
    KB_CHUNK_STRATEGY,
    chunk,
    deep_merge_mapping,
    merge_query_execution_params,
    normalize_mysql_processing_params,
    resolve_effective_processing_params,
)


def test_deep_merge_recurses_nested_dict():
    base = {"a": {"x": 1}, "b": 2}
    over = {"a": {"y": 3}}
    got = deep_merge_mapping(base, over)
    assert got["a"]["x"] == 1 and got["a"]["y"] == 3
    assert got["b"] == 2


def test_resolve_processing_priority_document_over_collection():
    merged = resolve_effective_processing_params(
        collection_defaults={"chunk_size": 800, "chunk_overlap": 50},
        document_overrides={"chunk_size": 600},
        request_once={"chunk_size": 400},
    )
    assert merged["chunk_size"] == 400
    assert merged["strategy"] == KB_CHUNK_STRATEGY


def test_normalize_mysql_processing_forces_markdown_strategy():
    out = normalize_mysql_processing_params({"strategy": "default", "chunk_size": 600})
    assert out["strategy"] == KB_CHUNK_STRATEGY
    assert out["chunk_size"] == 600


def test_request_once_without_document_keeps_defaults():
    m = resolve_effective_processing_params(
        collection_defaults={"chunk_size": 500},
        document_overrides=None,
        request_once={"chunk_overlap": 10},
    )
    assert m["chunk_size"] == 500 and m["chunk_overlap"] == 10


def test_merge_query_execution_params_respects_none_skip():
    base = {"limit": 7, "score_threshold": 0.3}
    out = merge_query_execution_params(
        persisted=base,
        request_overrides={"limit": None, "score_threshold": 0.5},
    )
    assert out["limit"] == 7
    assert out["score_threshold"] == 0.5


def test_chunk_long_plain_text_produces_chunks():
    docs = chunk(
        "a" * 120,
        effective_params={"chunk_size": 40, "chunk_overlap": 10},
        source_hint="t.txt",
    )
    assert len(docs) >= 1
