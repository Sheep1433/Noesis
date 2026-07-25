"""DeepDoc 解析产物契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DeepDocBlock:
    content: str
    page_no: Optional[int] = None
    layout_type: Optional[str] = None
    bbox: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeepDocTable:
    content: str
    page_no: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeepDocParseResult:
    source_file_name: str
    file_path: str
    file_type: str
    blocks: List[DeepDocBlock] = field(default_factory=list)
    tables: List[DeepDocTable] = field(default_factory=list)
    figures: List[DeepDocBlock] = field(default_factory=list)
    parser_id: str = "deepdoc"
    deepdoc_version: str = ""
    update_time: str = ""
    domain: Optional[str] = None
    business: Optional[str] = None

    def to_markdown(self) -> str:
        parts: List[str] = []
        for block in self.blocks:
            text = (block.content or "").strip()
            if text:
                parts.append(text)
        for table in self.tables:
            text = (table.content or "").strip()
            if text:
                parts.append(text)
        for figure in self.figures:
            text = (figure.content or "").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)
