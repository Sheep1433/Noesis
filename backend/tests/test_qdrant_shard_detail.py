"""Qdrant 分片详情字段：向量维度与入库时间。"""
from kb.retrieval.payload import payload_created_at, vector_length


def test_payload_created_at_prefers_created_at():
    assert payload_created_at({"created_at": "2026-01-01T00:00:00"}) == "2026-01-01T00:00:00"


def test_payload_created_at_falls_back_to_update_time():
    assert payload_created_at({"update_time": "2026-02-02T12:00:00"}) == "2026-02-02T12:00:00"


def test_payload_created_at_missing_returns_none():
    assert payload_created_at({}) is None


def test_vector_length_list():
    assert vector_length([0.0] * 1024) == 1024


def test_vector_length_named_vectors():
    assert vector_length({"default": [0.0] * 512}) == 512
