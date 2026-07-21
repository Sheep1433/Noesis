"""知识库入库/检索参数合并单测（rag-chunking-pipeline）。"""
from kb.chunk import (
    KB_CHUNK_PRESET_GENERAL,
    chunk,
    deep_merge_mapping,
    merge_query_execution_params,
    normalize_collection_processing_params,
    normalize_collection_query_params,
    normalize_query_execution_params,
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
        collection_defaults={"chunk_parser_config": {"chunk_size": 800, "chunk_overlap": 50}},
        document_overrides={"chunk_parser_config": {"chunk_size": 600}},
        request_once={"chunk_parser_config": {"chunk_size": 400}},
    )
    assert merged["chunk_parser_config"]["chunk_size"] == 400
    assert merged["chunk_preset_id"] == KB_CHUNK_PRESET_GENERAL


def test_normalize_collection_processing_forces_general_preset():
    out = normalize_collection_processing_params({"strategy": "markdown_headers", "chunk_size": 600})
    assert out["chunk_preset_id"] == KB_CHUNK_PRESET_GENERAL
    assert out["chunk_parser_config"]["chunk_size"] == 600


def test_request_once_without_document_keeps_defaults():
    m = resolve_effective_processing_params(
        collection_defaults={"chunk_parser_config": {"chunk_size": 500}},
        document_overrides=None,
        request_once={"chunk_parser_config": {"chunk_overlap": 10}},
    )
    assert m["chunk_parser_config"]["chunk_size"] == 500
    assert m["chunk_parser_config"]["chunk_overlap"] == 10


def test_merge_query_execution_params_limit_alias():
    base = {"final_top_k": 7, "score_threshold": 0.3}
    out = merge_query_execution_params(
        persisted=base,
        request_overrides={"limit": 12, "score_threshold": 0.5},
    )
    assert out["final_top_k"] == 12
    assert out["score_threshold"] == 0.5


def test_normalize_query_defaults_hybrid():
    params = normalize_query_execution_params()
    assert params["search_mode"] == "hybrid"
    assert params["use_reranker"] is True
    assert params["final_top_k"] == 10
    assert params["recall_top_k"] == 20
    assert params["rerank_top_k"] == 15


def test_chunk_long_plain_text_produces_chunks():
    docs = chunk(
        "a" * 120,
        effective_params={"chunk_parser_config": {"chunk_size": 40, "chunk_overlap": 10}},
        source_hint="t.txt",
    )
    assert len(docs) >= 1


def test_normalize_collection_query_maps_limit_alias():
    out = normalize_collection_query_params({"limit": 8})
    assert out["final_top_k"] == 8
    assert "limit" not in out
