"""Markdown 结构 outline 与预览提取。"""

from __future__ import annotations

import re
from typing import List, Tuple

_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)


def extract_preview(text: str, max_chars: int = 500) -> str:
    """返回正文前若干字符作为预览。"""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    if len(stripped) <= max_chars:
        return stripped
    return stripped[:max_chars] + "…"


def extract_outline(text: str, max_headings: int = 20) -> str:
    """
    从 Markdown 提取标题层级 outline。
    若无标题则返回空字符串。
    """
    if not text:
        return ""

    lines: List[str] = []
    for match in _HEADING_RE.finditer(text):
        level = len(match.group(1))
        title = match.group(2).strip()
        indent = "  " * (level - 1)
        lines.append(f"{indent}- {title}")
        if len(lines) >= max_headings:
            lines.append("  …")
            break
    return "\n".join(lines)


def split_lines(text: str) -> List[str]:
    return (text or "").splitlines()


def read_line_range(text: str, offset: int = 0, limit: int = 2000) -> Tuple[str, int, int, int]:
    """
    按行分页读取 Markdown。
    返回 (片段文本, offset, 返回行数, 总行数)。
    """
    lines = split_lines(text)
    total = len(lines)
    if total == 0:
        return "", 0, 0, 0
    start = max(0, offset)
    end = min(total, start + max(1, limit))
    chunk = "\n".join(lines[start:end])
    return chunk, start, end - start, total
