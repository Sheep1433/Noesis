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
    """单源 Skills 目录摘要（不含服务器路径）"""
    root_exists: bool = Field(description='该分类下是否有可用目录')
    writable: bool = Field(description='当前用户是否可写入')
    skill_count: int = Field(default=0, description='顶层技能包数量')
    tree: List[SkillFsTreeNode] = Field(default_factory=list, description='该源下树')


class SkillFsTreeResponse(BaseModel):
    """Skills 目录树响应（平台 + 用户）"""
    platform: SkillFsSourceSection = Field(description='平台 Skills')
    user: SkillFsSourceSection = Field(description='当前用户 Skills')
    tree: List[SkillFsTreeNode] = Field(
        default_factory=list,
        description='合并展示树（顶层「平台预置」「个人技能」）',
    )


class SkillFsFileContent(BaseModel):
    """Skills 目录下文件内容"""
    rel_path: str = Field(description='技能包内相对路径，如 arxiv/SKILL.md')
    filename: str = Field(description='文件名')
    source: SkillSource = Field(description='platform 或 user')
    content: str = Field(description='文件文本内容')


class SkillMarketItem(BaseModel):
    """skills.sh 市场条目"""
    id: str = Field(description='完整 id，如 anthropics/skills/pdf')
    skill_id: str = Field(description='技能包名（安装目录名）')
    name: str = Field(description='展示名')
    source: str = Field(description='GitHub owner/repo，如 anthropics/skills')
    installs: int = Field(default=0, description='skills.sh 安装次数')
    market_url: str = Field(default='', description='skills.sh 详情页')
    installed: bool = Field(
        default=False,
        description='是否已从同源安装到个人 skills（exact）',
    )
    install_match: Literal['none', 'exact', 'name_conflict'] = Field(
        default='none',
        description='none 未占用；exact 同源已装；name_conflict 同名目录已占用',
    )


class SkillMarketListResponse(BaseModel):
    """市场列表（browse / search）"""
    items: List[SkillMarketItem] = Field(default_factory=list)
    query: str = Field(default='', description='搜索词；browse 为空')


class SkillMarketDetailResponse(BaseModel):
    """市场技能详情（正文来自 skills.sh 详情页）"""
    item: SkillMarketItem
    skill_md: str = Field(default='', description='SKILL 正文（skills.sh 渲染）')
    skill_md_path: str = Field(default='', description='展示用路径，固定为 SKILL.md')


class SkillMarketInstallRequest(BaseModel):
    """从市场安装到个人 skills"""
    source: str = Field(description='GitHub owner/repo')
    skill_id: str = Field(description='技能包名')
    overwrite: bool = Field(default=False, description='同名已存在时是否覆盖')
