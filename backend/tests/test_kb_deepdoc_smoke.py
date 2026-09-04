"""DeepDoc 多格式冒烟（mock parser，不依赖 ONNX）。"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from noesis.knowledge.parser.deepdoc_result import DeepDocBlock, DeepDocParseResult
from noesis.knowledge.parser.factory import ParserFactory


def _fake_result(file_path: str, *, blocks=None, file_type="pdf") -> DeepDocParseResult:
    import os
    from datetime import datetime

    name = os.path.basename(file_path)
    return DeepDocParseResult(
        source_file_name=name,
        file_path=file_path,
        file_type=file_type,
        blocks=blocks or [DeepDocBlock(content="mock content", layout_type="text")],
        update_time=datetime.now().isoformat(),
        deepdoc_version="test-pin",
    )


@pytest.mark.parametrize(
    "suffix,ftype",
    [
        (".pdf", "pdf"),
        (".docx", "docx"),
        (".pptx", "pptx"),
    ],
)
def test_deepdoc_smoke_mock_formats(tmp_path, suffix, ftype):
    path = tmp_path / f"sample{suffix}"
    path.write_bytes(b"fake")

    with patch(
        "noesis.knowledge.parser.factory.parse_file_with_deepdoc",
        return_value=_fake_result(str(path), file_type=ftype),
    ):
        parsed = ParserFactory.parse(str(path))

    assert parsed.deepdoc_result is not None
    assert parsed.file_type == ftype
    assert "mock content" in (parsed.raw_markdown or "")


def test_excel_smoke_mock_rows(tmp_path):
    pytest.importorskip("openpyxl")
    import pandas as pd

    xlsx = tmp_path / "rows.xlsx"
    pd.DataFrame([{"a": 1}]).to_excel(xlsx, index=False)
    result = _fake_result(
        str(xlsx),
        file_type="xlsx",
        blocks=[DeepDocBlock(content="a: 1", layout_type="table_row")],
    )
    with patch("noesis.knowledge.parser.factory.parse_file_with_deepdoc", return_value=result):
        parsed = ParserFactory.parse(str(xlsx))
    assert parsed.is_tabular
    assert parsed.row_documents


# ---------------------------------------------------------------------------
# PDF 重栈缺失降级（optional extra deepdoc-pdf 未安装的生产镜像）
# ---------------------------------------------------------------------------

class TestPdfFallbackWithoutHeavyStack:
    def test_pdf_falls_back_to_pdfplumber_when_stack_missing(self, tmp_path, monkeypatch):
        """cv2/onnxruntime 不可用 → pdfplumber 纯文本抽取（每页一个 text block）。"""
        from noesis.knowledge.parser import deepdoc_service

        pdf = tmp_path / "sample.pdf"
        pdf.write_bytes(_minimal_pdf_bytes())

        monkeypatch.setattr(deepdoc_service, "_deepdoc_pdf_stack_available", lambda: False)
        blocks, tables, figures = deepdoc_service._parse_pdf(str(pdf))

        assert not tables and not figures
        assert blocks, "降级路径应产出文本 block"
        assert all(b.layout_type == "text" for b in blocks)
        assert "Hello" in blocks[0].content

    def test_stack_available_check_uses_import(self, monkeypatch):
        """可用性探测按 import 成败判定（本地已装 → True）。"""
        from noesis.knowledge.parser import deepdoc_service

        assert deepdoc_service._deepdoc_pdf_stack_available() is True

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "cv2":
                raise ImportError("no cv2 in slim image")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert deepdoc_service._deepdoc_pdf_stack_available() is False


def _minimal_pdf_bytes() -> bytes:
    """最小可解析 PDF（单页 Hello 文本），pdfplumber 可抽取。"""
    content = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100]
 /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 44 >>
stream
BT /F1 12 Tf 20 50 Td (Hello pdfplumber) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R >>
%%EOF"""
    return content
