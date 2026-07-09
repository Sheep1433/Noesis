"""会话上下文面板 API 模型。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class FsTreeNode(BaseModel):
    key: str = Field(description='相对用户根的路径，如 skills/foo/SKILL.md 或 sessions/{id}/workspace/report.md')
    label: str = Field(description='显示名')
    isLeaf: bool = Field(default=False)
    children: Optional[List[FsTreeNode]] = Field(default=None)


class SessionContextResponse(BaseModel):
    tree: List[FsTreeNode] = Field(
        default_factory=list,
        description='用户目录浏览树（skills、记忆文件 + 当前 sessions/{id}/workspace|uploads）',
    )
    session_root_path: str = Field(default="")


class WorkspaceFileContent(BaseModel):
    path: str = Field(description='相对用户根的路径')
    content: str = Field(description='文本内容')


class WorkspaceFileWriteRequest(BaseModel):
    path: str = Field(description='相对用户根的路径')
    content: str = Field(description='待写入的 UTF-8 文本')
