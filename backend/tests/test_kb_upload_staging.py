"""知识库上传暂存与原始文件名元数据。"""
from __future__ import annotations

from unittest.mock import patch

from kb.document_parse.cached_parse import parse_file_cached
from kb.document_parse.deepdoc_result import DeepDocBlock, DeepDocParseResult
from kb.document_parse.source_name import apply_source_file_name
from kb.document_parse.staging import sanitize_kb_filename, staging_path, write_staging
from kb.rerank.client import is_rerank_available


def test_sanitize_kb_filename_strips_path():
    assert sanitize_kb_filename("/tmp/evil/../需求.docx") == "需求.docx"


def test_staging_path_under_data_dir():
    path = staging_path("my_col", "abc" * 10, "需求文档.docx")
    assert path.name.endswith("_需求文档.docx")
    assert "kb_uploads" in path.parts
    assert "my_col" in path.parts


def test_write_staging_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kb.document_parse.staging.data_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )
    content = b"hello"
    path, file_hash = write_staging("col", content, "note.md")
    assert path.is_file()
    assert path.read_bytes() == content
    assert len(file_hash) == 64


def test_apply_source_file_name_updates_parsed():
    from kb.document_parse.models import ParsedFile

    parsed = ParsedFile(
        file_path="/tmp/tmpXXXX.md",
        file_name="tmpXXXX.md",
        file_type="md",
        update_time="",
        deepdoc_result=DeepDocParseResult(
            source_file_name="tmpXXXX.md",
            file_path="/tmp/tmpXXXX.md",
            file_type="md",
        ),
    )
    updated = apply_source_file_name(parsed, "真实需求.md")
    assert updated.file_name == "真实需求.md"
    assert updated.deepdoc_result is not None
    assert updated.deepdoc_result.source_file_name == "真实需求.md"


@patch("kb.document_parse.cached_parse.parse_file_with_deepdoc")
def test_parse_file_cached_uses_source_file_name(mock_parse, tmp_path):
    staging = tmp_path / "hash_真实.docx"
    staging.write_text("x", encoding="utf-8")
    mock_parse.return_value = DeepDocParseResult(
        source_file_name="真实.docx",
        file_path=str(staging),
        file_type="docx",
        blocks=[DeepDocBlock(content="正文", layout_type="text")],
    )

    parsed = parse_file_cached(
        str(staging),
        collection_name="col",
        file_hash="deadbeef",
        source_file_name="真实.docx",
        use_cache=False,
    )
    assert parsed.file_name == "真实.docx"
    mock_parse.assert_called_once()
    assert mock_parse.call_args.kwargs.get("source_file_name") == "真实.docx"


def test_rerank_api_key_falls_back_to_embedding():
    from unittest.mock import Mock

    from config.env import _build_model
    from config.yaml_config import load_app_yaml

    secrets = Mock()
    secrets.rerank_model_api_key = ""
    secrets.embedding_model_api_key = "sk-emb"
    yaml_cfg = load_app_yaml()
    model = _build_model(secrets, yaml_cfg)
    assert model.rerank_model_api_key == "sk-emb"


@patch("kb.rerank.client.ModelConfig")
def test_is_rerank_available_uses_config_key(mock_cfg):
    mock_cfg.rerank_model_name = "gte-rerank-v2"
    mock_cfg.rerank_model_api_key = "sk-emb"
    assert is_rerank_available() is True
