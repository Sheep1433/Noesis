"""文档解析层数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from langchain_core.documents import Document


@dataclass
class ParsedFile:
    """parse 层输出：Markdown 或表格行级 Document，不含分块。"""

    file_path: str
    file_name: str
    file_type: str
    update_time: str
    domain: Optional[str] = None
    business: Optional[str] = None
    raw_markdown: Optional[str] = None
    clean_markdown: Optional[str] = None
    row_documents: Optional[List[Document]] = field(default=None)

    @property
    def is_tabular(self) -> bool:
        return self.row_documents is not None
