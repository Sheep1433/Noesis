"""DeepDoc 多格式冒烟（mock parser，不依赖 ONNX）。"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from kb.document_parse.deepdoc_result import DeepDocBlock, DeepDocParseResult
from kb.document_parse.factory import ParserFactory


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
        "kb.document_parse.factory.parse_file_with_deepdoc",
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
    with patch("kb.document_parse.factory.parse_file_with_deepdoc", return_value=result):
        parsed = ParserFactory.parse(str(xlsx))
    assert parsed.is_tabular
    assert parsed.row_documents
