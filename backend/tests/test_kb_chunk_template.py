"""chunk_template_id 规范化单测。"""
from kb.chunk import (
    KB_CHUNK_TEMPLATE_GENERAL,
    normalize_mysql_processing_params,
)


def test_unknown_template_falls_back_to_general():
    out = normalize_mysql_processing_params({"chunk_template_id": "book"})
    assert out["chunk_template_id"] == KB_CHUNK_TEMPLATE_GENERAL
    assert out["chunk_preset_id"] == KB_CHUNK_TEMPLATE_GENERAL


def test_general_template_preserved():
    out = normalize_mysql_processing_params({"chunk_template_id": "general"})
    assert out["chunk_template_id"] == "general"
