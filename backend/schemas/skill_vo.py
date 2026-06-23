"""
Skills 文件目录 API 的 Pydantic 模型
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

SkillSource = Literal["platform", "user"]


class SkillFsTreeNode(BaseModel):
    """Skills 目录树节点（供前端 n-tree 使用）"""
    key: str = Field(description='节点 key（含 platform:/user: 前缀）')
    label: str = Field(description='节点显示名')
    isLeaf: bool = Field(default=False, description='是否为文件')
    children: Optional[List[SkillFsTreeNode]] = Field(default=None, description='子节点')
    source: SkillSource = Field(default='platform', description='platform 或 user')


class SkillFsSourceSection(BaseModel):
    """单源 Skills 目录"""
    root_path: str = Field(description='根目录绝对路径')
    root_exists: bool = Field(description='根路径是否为已存在的目录')
    tree: List[SkillFsTreeNode] = Field(default_factory=list, description='该源下树')


class SkillFsTreeResponse(BaseModel):
    """Skills 目录树响应（平台 + 用户）"""
    platform: SkillFsSourceSection = Field(description='平台 Skills')
    user: SkillFsSourceSection = Field(description='当前用户 Skills')
    tree: List[SkillFsTreeNode] = Field(
        default_factory=list,
        description='合并展示树（顶层「平台技能」「我的技能」）',
    )


class SkillFsFileContent(BaseModel):
    """Skills 目录下文件内容"""
    path: str = Field(description='请求路径（含 source 前缀）')
    source: SkillSource = Field(description='platform 或 user')
    content: str = Field(description='文件文本内容')
