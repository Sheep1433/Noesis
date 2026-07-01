"""parse_cache 单测。"""
from kb.document_parse.deepdoc_result import DeepDocBlock, DeepDocParseResult
from kb.document_parse.parse_cache import (
    cache_file_path,
    load_parse_cache,
    result_from_dict,
    save_parse_cache,
)


def test_parse_cache_roundtrip(tmp_path, monkeypatch):
    root = tmp_path / "kb_parse"
    monkeypatch.setattr(
        "kb.document_parse.parse_cache._cache_root",
        lambda: root,
    )
    result = DeepDocParseResult(
        source_file_name="a.md",
        file_path="/tmp/a.md",
        file_type="md",
        blocks=[DeepDocBlock(content="# hi", layout_type="markdown")],
        deepdoc_version="828c5789f651",
    )
    save_parse_cache("col1", "abc123", result)
    path = cache_file_path("col1", "abc123")
    assert path.is_file()

    loaded = load_parse_cache("col1", "abc123")
    assert loaded is not None
    assert loaded.blocks[0].content == "# hi"

    restored = result_from_dict({"source_file_name": "x", "file_path": "p", "file_type": "md"})
    assert restored.source_file_name == "x"
