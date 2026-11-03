"""DeepDoc 集成单测（不依赖 ONNX 权重）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from noesis.knowledge.chunking import chunk, fixed_processing_params
from noesis.knowledge.chunking.deepdoc_adapter import adapt_deepdoc_to_documents
from noesis.knowledge.parser import DocumentParser
from noesis.knowledge.parser.deepdoc_result import DeepDocBlock, DeepDocParseResult
from noesis.knowledge.parser.factory import ParserFactory
from noesis.knowledge.parser.models import ParsedFile

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


def test_parse_txt_returns_whole_text_as_single_block(tmp_path):
    """纯文本整篇一个 block：短文单 chunk，超长固定窗口（与评测直写口径一致）。"""
    from noesis.knowledge.parser.deepdoc_service import _parse_txt

    body = "\n\n".join(f"段落{i}，" + "正文内容" * 30 for i in range(30))
    f = tmp_path / "doc.txt"
    f.write_text(body, encoding="utf-8")

    blocks = _parse_txt(str(f))

    assert len(blocks) == 1
    assert blocks[0].content == body.strip()
    assert (blocks[0].layout_type or "").lower() == "text"


def test_fixed_window_chunks_align_to_paragraph_boundaries() -> None:
    """固定窗口切分：窗口尾部优先在段落边界断开，不腰斩段落。"""
    from noesis.knowledge.chunking.params import _fixed_window_chunks

    paragraphs = [f"第{i}段" + "甲" * 96 for i in range(10)]  # 每段 100 字符
    body = "\n\n".join(paragraphs)  # 1000 字符含分隔

    chunks = _fixed_window_chunks(body, chunk_size=250, overlap=25)

    assert len(chunks) >= 3
    # 游标逐块验证：chunk 内容与滑窗游标逐位吻合（覆盖无缺口）；
    # 边界落在段落分隔之后（chunk 尾部含换行）——不出现段中截断
    pos = 0
    for c in chunks[:-1]:
        assert body.find(c, pos) == pos
        end = pos + len(c)
        assert body[end - 1] == "\n"
        pos = max(end - 25, pos + 1)
    assert body.find(chunks[-1], pos) == pos
    assert body.endswith(chunks[-1])


def test_txt_ingestion_whole_doc_and_window_paths(tmp_path):
    """txt 入库端到端：短文整篇单 chunk；超长走固定窗口 + 段落对齐。"""
    from noesis.knowledge.parser.deepdoc_service import parse_file_with_deepdoc

    def _to_parsed(result):
        return ParsedFile(
            file_path=result.file_path,
            file_name=result.source_file_name,
            file_type=result.file_type,
            update_time=result.update_time,
            raw_markdown=result.to_markdown(),
            clean_markdown=result.to_markdown(),
            deepdoc_result=result,
        )

    # 短文（< chunk_size）：整篇一个 chunk
    short = "短文档正文，一段就够。"
    sf = tmp_path / "short.txt"
    sf.write_text(short, encoding="utf-8")
    result = parse_file_with_deepdoc(str(sf), source_file_name="short.txt")
    docs = adapt_deepdoc_to_documents(
        _to_parsed(result),
        effective_params={"chunk_parser_config": {"chunk_size": 500, "chunk_overlap": 50}},
    )
    assert len(docs) == 1
    assert docs[0].page_content == short

    # 超长：固定窗口切分，边界对齐段落。
    # 段长取 ~113 字符（< 窗口 600 的尾部 25%），保证尾部区间必含边界；
    # 段长超过窗口四分之一时按声明规则退硬切（无分隔文本行为由
    # test_deepdoc_adapter_splits_oversized_single_block 覆盖）
    paragraphs = [f"第{i}段" + "乙" * 110 for i in range(40)]
    body = "\n\n".join(paragraphs)
    lf = tmp_path / "long.txt"
    lf.write_text(body, encoding="utf-8")
    result = parse_file_with_deepdoc(str(lf), source_file_name="long.txt")
    docs = adapt_deepdoc_to_documents(
        _to_parsed(result),
        effective_params={"chunk_parser_config": {"chunk_size": 600, "chunk_overlap": 60}},
    )
    assert len(docs) >= 3
    # 不腰斩：每个片段（按 \n\n 切）都是某段的完整后缀——任何段中截断
    # 或跨段粘连都会让 endswith 失败；overlap 只允许出现在段首前缀上
    pieces = [piece for d in docs for piece in d.page_content.split("\n\n") if piece]
    assert all(any(p.endswith(piece) for p in paragraphs) for piece in pieces)
    # 不丢失：每个段落完整出现在至少一个 chunk 里
    assert all(any(p in d.page_content for d in docs) for p in paragraphs)


def test_parse_txt_decodes_gbk_fallback(tmp_path):
    """GBK 编码的中文 txt 可入库：UTF-8 解码失败退 GBK，不静默乱码。"""
    from noesis.knowledge.parser.deepdoc_service import _parse_txt

    f = tmp_path / "gbk.txt"
    f.write_bytes("中文内容，GBK 编码。".encode("gbk"))

    blocks = _parse_txt(str(f))

    assert len(blocks) == 1
    assert "中文内容，GBK 编码。" in blocks[0].content
