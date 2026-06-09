"""COMMON_QA 会话附件工具：read_attachment / grep_attachment。"""

from __future__ import annotations

import re
from typing import List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import ChatAttachmentConfig
from services.chat_attachment_service import ChatAttachmentService
from utils.markdown_outline import read_line_range


class ReadAttachmentInput(BaseModel):
    path: str = Field(description="附件 virtual_path 或 file_name")
    offset: int = Field(default=0, ge=0, description="起始行号（0-based）")
    limit: int = Field(
        default=ChatAttachmentConfig.read_page_lines,
        ge=1,
        le=5000,
        description="读取行数上限",
    )


class GrepAttachmentInput(BaseModel):
    pattern: str = Field(description="正则表达式")
    path: Optional[str] = Field(default=None, description="限定 virtual_path 或 file_name；省略则搜索全部文档")


def build_attachment_tools(
    *,
    session_id: str,
    user_id: str,
    db: AsyncSession,
) -> List[StructuredTool]:
    async def read_attachment(path: str, offset: int = 0, limit: int = ChatAttachmentConfig.read_page_lines) -> str:
        row = await ChatAttachmentService.find_document(
            session_id=session_id,
            user_id=user_id,
            path=path,
            db=db,
        )
        if not row:
            return f"未找到文档附件: {path}"
        text, _ = ChatAttachmentService._read_document_text(row)
        if not text.strip():
            return f"文档 {row.file_name} 正文为空"
        chunk, start, count, total = read_line_range(text, offset=offset, limit=limit)
        return (
            f"文件: {row.file_name}\n"
            f"路径: {row.virtual_path}\n"
            f"行范围: {start}–{start + count - 1} / 共 {total} 行\n\n"
            f"{chunk}"
        )

    async def grep_attachment(pattern: str, path: Optional[str] = None) -> str:
        try:
            regex = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            return f"无效正则: {exc}"

        rows = await ChatAttachmentService.list_session_documents(session_id, user_id, db)
        if path:
            row = await ChatAttachmentService.find_document(
                session_id=session_id, user_id=user_id, path=path, db=db
            )
            rows = [row] if row else []

        if not rows:
            return "当前会话无可用文档附件"

        hits: List[str] = []
        for row in rows:
            text, _ = ChatAttachmentService._read_document_text(row)
            if not text:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{row.file_name}:{line_no}: {line.strip()}")
                    if len(hits) >= 50:
                        hits.append("…（命中过多，已截断）")
                        return "\n".join(hits)
        if not hits:
            return f"未匹配 pattern={pattern!r}"
        return "\n".join(hits)

    return [
        StructuredTool.from_function(
            coroutine=read_attachment,
            name="read_attachment",
            description="分页读取会话内文档附件的 Markdown 正文（按行 offset/limit）",
            args_schema=ReadAttachmentInput,
        ),
        StructuredTool.from_function(
            coroutine=grep_attachment,
            name="grep_attachment",
            description="在会话文档附件中按正则搜索行",
            args_schema=GrepAttachmentInput,
        ),
    ]
