"""DeepDoc 集成单测（不依赖 ONNX 权重）。"""
from __future__ import annotations

from kb.chunk.deepdoc_adapter import adapt_deepdoc_to_documents
from kb.document_parse.deepdoc_result import DeepDocBlock, DeepDocParseResult
from kb.document_parse.factory import ParserFactory
from kb.document_parse.models import ParsedFile


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


def test_parse_markdown_via_deepdoc(tmp_path):
    md = tmp_path / "req.md"
    md.write_text("# 标题\n\n正文", encoding="utf-8")
    parsed = ParserFactory.parse(str(md))
    assert parsed.deepdoc_result is not None
    assert "标题" in (parsed.raw_markdown or "")
