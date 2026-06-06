"""document_parse 层单测：仅验证 parse，不含分块。"""
from __future__ import annotations

import pytest

from kb.document_parse import DocumentParser


def test_excel_parse_each_row_is_document(tmp_path):
    pytest.importorskip("openpyxl")
    import pandas as pd

    xlsx_path = tmp_path / "cases.xlsx"
    pd.DataFrame(
        [
            {"场景": "登录", "步骤": "输入账号"},
            {"场景": "登出", "步骤": "点击退出"},
        ]
    ).to_excel(xlsx_path, index=False)

    parsed = DocumentParser.parse_file(str(xlsx_path))

    assert parsed.is_tabular
    assert len(parsed.row_documents) == 2
    assert parsed.row_documents[0].metadata["element_type"] == "table"
    assert "登录" in parsed.row_documents[0].page_content
    assert "登出" in parsed.row_documents[1].page_content


def test_markdown_parse_returns_content(tmp_path):
    md_path = tmp_path / "req.md"
    md_path.write_text("# 标题\n\n## 章节\n\n正文内容。", encoding="utf-8")

    parsed = DocumentParser.parse_file(str(md_path))

    assert not parsed.is_tabular
    assert parsed.file_name == "req.md"
    assert "标题" in (parsed.raw_markdown or "")


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("xlwt") is None,
    reason="xlwt 未安装",
)
def test_xls_parse_each_row_is_document(tmp_path):
    import pandas as pd

    xls_path = tmp_path / "cases.xls"
    pd.DataFrame([{"A": 1}, {"A": 2}]).to_excel(xls_path, index=False, engine="xlwt")

    parsed = DocumentParser.parse_file(str(xls_path))

    assert parsed.is_tabular
    assert len(parsed.row_documents) == 2
    assert parsed.file_type == "xls"
