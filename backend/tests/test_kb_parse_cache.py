"""parse_cache 单测。"""
from noesis.knowledge.parser.deepdoc_result import DeepDocBlock, DeepDocParseResult
from noesis.knowledge.parser.parse_cache import (
    cache_file_path,
    load_parse_cache,
    result_from_dict,
    save_parse_cache,
)


def test_parse_cache_roundtrip(tmp_path, monkeypatch):
    root = tmp_path / "kb_parse"
    monkeypatch.setattr(
        "noesis.knowledge.parser.parse_cache._cache_root",
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


def test_parse_cache_ignores_entries_from_old_parse_behavior(tmp_path, monkeypatch):
    """解析行为版本失配的缓存必须被忽略——解析代码行为变更后，
    旧缓存会静默回放旧切块（10k 重导入实测踩过：段落对齐 0/434）。"""
    import json

    root = tmp_path / "kb_parse"
    monkeypatch.setattr(
        "noesis.knowledge.parser.parse_cache._cache_root",
        lambda: root,
    )
    # 旧格式缓存：无 parse_behavior_version 字段（deepdoc_version 照常匹配）
    path = cache_file_path("col1", "abc123")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "source_file_name": "a.txt",
        "file_path": "/tmp/a.txt",
        "file_type": "txt",
        "blocks": [{"content": "旧解析结果", "layout_type": "text"}],
        "deepdoc_version": "828c5789f651",
    }), encoding="utf-8")

    assert load_parse_cache("col1", "abc123") is None

    # 新格式（行为版本匹配）正常命中
    result = DeepDocParseResult(
        source_file_name="a.txt",
        file_path="/tmp/a.txt",
        file_type="txt",
        blocks=[DeepDocBlock(content="新解析结果", layout_type="text")],
        deepdoc_version="828c5789f651",
    )
    save_parse_cache("col1", "def456", result)
    loaded = load_parse_cache("col1", "def456")
    assert loaded is not None
    assert loaded.blocks[0].content == "新解析结果"
