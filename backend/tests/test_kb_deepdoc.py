"""DeepDoc 集成单测（不依赖 ONNX 权重）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from kb.chunk import chunk, fixed_processing_params
from kb.chunk.deepdoc_adapter import adapt_deepdoc_to_documents
from kb.document_parse import DocumentParser
from kb.document_parse.deepdoc_result import DeepDocBlock, DeepDocParseResult
from kb.document_parse.factory import ParserFactory
from kb.document_parse.models import ParsedFile

_SEED_MEDICAL_MD = (
    Path(__file__).resolve().parents[1]
    / "kb/seed/chinese_medical_docs/01_胃食管反流病与食管癌.md"
)


def test_factory_rejects_non_deepdoc_parser(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("# hi", encoding="utf-8")
    try:
        ParserFactory.parse(str(md), parser_id="markitdown")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "deepdoc" in str(exc)


def test_deepdoc_adapter_merges_blocks():
    result = DeepDocParseResult(
        source_file_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        file_type="pdf",
        blocks=[
            DeepDocBlock(content="第一段", page_no=1, layout_type="text"),
            DeepDocBlock(content="第二段", page_no=1, layout_type="text"),
        ],
    )
    parsed = ParsedFile(
        file_path=result.file_path,
        file_name=result.source_file_name,
        file_type=result.file_type,
        update_time="now",
        raw_markdown=result.to_markdown(),
        clean_markdown=result.to_markdown(),
        deepdoc_result=result,
    )
    docs = adapt_deepdoc_to_documents(parsed, effective_params={"chunk_size": 500, "chunk_overlap": 0})
    assert len(docs) == 1
    assert "第一段" in docs[0].page_content
    assert docs[0].metadata.get("page_no") == 1


def test_deepdoc_adapter_splits_oversized_single_block():
    big = "甲" * 1200
    result = DeepDocParseResult(
        source_file_name="doc.md",
        file_path="/tmp/doc.md",
        file_type="md",
        blocks=[DeepDocBlock(content=big, layout_type="text")],
    )
    parsed = ParsedFile(
        file_path=result.file_path,
        file_name=result.source_file_name,
        file_type=result.file_type,
        update_time="now",
        raw_markdown=result.to_markdown(),
        clean_markdown=result.to_markdown(),
        deepdoc_result=result,
    )
    docs = adapt_deepdoc_to_documents(
        parsed,
        effective_params={"chunk_parser_config": {"chunk_size": 500, "chunk_overlap": 50}},
    )
    assert len(docs) >= 3
    assert all(len(d.page_content) <= 500 for d in docs)


def test_parse_markdown_via_deepdoc(tmp_path):
    md = tmp_path / "req.md"
    md.write_text("# 标题\n\n正文", encoding="utf-8")
    parsed = ParserFactory.parse(str(md))
    assert parsed.deepdoc_result is not None
    assert len(parsed.deepdoc_result.blocks) >= 2
    assert "标题" in (parsed.raw_markdown or "")


@pytest.mark.skipif(not _SEED_MEDICAL_MD.is_file(), reason="医疗 seed 文档缺失")
def test_chunk_medical_markdown_produces_many_shards():
    parsed = DocumentParser.parse_file(str(_SEED_MEDICAL_MD))
    docs = chunk(parsed, effective_params=fixed_processing_params())
    lens = [len(d.page_content) for d in docs]
    assert len(docs) >= 20
    # 合并相邻 block 时单片可能略超 chunk_size，但不应再出现整篇级巨型分片
    assert max(lens) <= 600
