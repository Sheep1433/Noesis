"""会话上下文面板 API 模型。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class FsTreeNode(BaseModel):
    key: str = Field(description='相对会话根的路径，如 workspace/report.md')
    label: str = Field(description='显示名')
    isLeaf: bool = Field(default=False)
    children: Optional[List[FsTreeNode]] = Field(default=None)


class SessionContextResponse(BaseModel):
    tree: List[FsTreeNode] = Field(
        default_factory=list,
        description='会话浏览树（sessions/{id}/workspace|uploads，不含内部 attachments 副本）',
    )
    session_root_path: str = Field(default="")


class WorkspaceFileContent(BaseModel):
    path: str = Field(description='相对会话根的路径')
    content: str = Field(description='文本内容')
