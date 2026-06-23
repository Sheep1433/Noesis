"""会话上下文面板 API 模型。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from schemas.chat_attachment_vo import AttachmentResponse


class FsTreeNode(BaseModel):
    key: str = Field(description='相对 workspace 根的路径')
    label: str = Field(description='显示名')
    isLeaf: bool = Field(default=False)
    children: Optional[List[FsTreeNode]] = Field(default=None)


class SessionContextResponse(BaseModel):
    workspace: List[FsTreeNode] = Field(default_factory=list)
    attachments: List[AttachmentResponse] = Field(default_factory=list)
    workspace_root_exists: bool = Field(default=False)
    workspace_root_path: str = Field(default="")


class WorkspaceFileContent(BaseModel):
    path: str = Field(description='相对 workspace 路径')
    content: str = Field(description='文本内容')
