"""文档解析层：任意格式 → Markdown / 表格行 Document（不含分块）。"""
from __future__ import annotations

from kb.document_parse.models import ParsedFile
from kb.document_parse.parser import DocumentParser

__all__ = ["DocumentParser", "ParsedFile"]
