"""chunk 层单测：parse 输出 / Markdown 文本 → 分片 Document。"""
import pytest

from kb.chunk import chunk, fixed_processing_params
from kb.document_parse import DocumentParser


def test_chunk_text_sets_file_metadata():
    docs = chunk(
        "hello world " * 20,
        effective_params={"chunk_size": 50, "chunk_overlap": 10},
        source_hint="doc.md",
    )
    assert docs
    assert docs[0].metadata.get("file_name") == "doc.md"


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("docx") is None,
    reason="python-docx 未安装",
)
def test_chunk_text_headers_populates_header_metadata():
    md = "# Title\n\n## Section\n\nBody text here."
    docs = chunk(
        md,
        effective_params={"chunk_size": 500, "chunk_overlap": 50},
        source_hint="doc.md",
    )
    assert docs
    meta = docs[0].metadata
    assert meta.get("file_name") == "doc.md"
    assert meta.get("header_path") or meta.get("Header_1") or meta.get("Header_2")


def test_chunk_parsed_markdown_file(tmp_path):
    md_path = tmp_path / "req.md"
    md_path.write_text("# 标题\n\n## 章节\n\n正文内容。", encoding="utf-8")

    parsed = DocumentParser.parse_file(str(md_path))
    docs = chunk(parsed, effective_params=fixed_processing_params())

    assert docs
    assert docs[0].metadata.get("file_name") == "req.md"
    assert docs[0].metadata.get("chunk_index") == 0

def test_chunk_fenced_code_hash_not_treated_as_header():
    md = """# 真实章节

正文

```python
# 这不是标题
x = 1
```

## 下一节
"""
    docs = chunk(
        md,
        effective_params={"chunk_size": 500, "chunk_overlap": 50},
        source_hint="doc.md",
    )
    assert docs
    # 不应仅因代码块内 # 行而拆出「这不是标题」为独立 header 章节
    header_paths = [d.metadata.get("header_path") or "" for d in docs]
    assert not any("这不是标题" in hp for hp in header_paths)
    assert any("真实章节" in hp for hp in header_paths)


def test_chunk_parsed_excel_rows(tmp_path):
    pytest.importorskip("openpyxl")
    import pandas as pd

    xlsx_path = tmp_path / "cases.xlsx"
    pd.DataFrame([{"场景": "登录"}, {"场景": "登出"}]).to_excel(xlsx_path, index=False)

    parsed = DocumentParser.parse_file(str(xlsx_path))
    docs = chunk(parsed, effective_params=fixed_processing_params())

    assert len(docs) == 2
    assert docs[0].metadata["chunk_index"] == 0
    assert docs[1].metadata["chunk_index"] == 1
