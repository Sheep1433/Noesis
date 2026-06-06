"""
Skills 文件目录 API 的 Pydantic 模型
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class SkillFsTreeNode(BaseModel):
    """Skills 目录树节点（供前端 n-tree 使用）"""
    key: str = Field(description='相对 skills 根目录的路径')
    label: str = Field(description='节点显示名')
    isLeaf: bool = Field(default=False, description='是否为文件')
    children: Optional[List[SkillFsTreeNode]] = Field(default=None, description='子节点')


class SkillFsTreeResponse(BaseModel):
    """Skills 目录树响应"""
    root_path: str = Field(description='解析后的 skills 根目录绝对路径')
    root_exists: bool = Field(description='根路径是否为已存在的目录')
    tree: List[SkillFsTreeNode] = Field(default_factory=list, description='根下第一层起的树')


class SkillFsFileContent(BaseModel):
    """Skills 目录下文件内容"""
    path: str = Field(description='相对根路径')
    content: str = Field(description='文件文本内容')
