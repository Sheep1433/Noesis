"""
Skills 文件目录（extensions/skills）浏览与 ZIP 导入
"""
import os
import zipfile
from typing import List, Tuple

from config.extensions_paths import skills_root
from schemas.skill_vo import SkillFsTreeNode, SkillFsTreeResponse
from common.logging import logger

_MAX_READ_BYTES = 512 * 1024
_MAX_ZIP_BYTES = 10 * 1024 * 1024


class SkillFsService:
    """扫描配置的 skills 根目录，提供树形结构与安全读文件"""

    @classmethod
    def get_root_path(cls) -> str:
        return str(skills_root())

    @classmethod
    def _safe_join(cls, rel: str) -> str:
        root = os.path.abspath(cls.get_root_path())
        rel = rel.replace('\\', '/').strip('/')
        if rel == '.' or rel.startswith('..') or '/../' in f'/{rel}/':
            raise ValueError('非法路径')
        parts = [p for p in rel.split('/') if p and p != '.']
        for p in parts:
            if p == '..':
                raise ValueError('非法路径')
        target = os.path.abspath(os.path.join(root, *parts))
        if target != root and not target.startswith(root + os.sep):
            raise ValueError('非法路径')
        return target

    @classmethod
    def _sort_nodes(cls, nodes: List[SkillFsTreeNode]) -> List[SkillFsTreeNode]:
        return sorted(nodes, key=lambda n: (n.isLeaf, n.label.lower()))

    @classmethod
    def _scan_dir(cls, rel: str) -> List[SkillFsTreeNode]:
        full = cls._safe_join(rel)
        if not os.path.isdir(full):
            return []
        entries: List[SkillFsTreeNode] = []
        try:
            names = sorted(os.listdir(full), key=lambda x: x.lower())
        except OSError as e:
            logger.warning(f'列出目录失败 {full}: {e}')
            return []
        for name in names:
            if name.startswith('.'):
                continue
            entry_rel = f'{rel}/{name}' if rel else name
            entry_full = os.path.join(full, name)
            try:
                if os.path.isdir(entry_full):
                    children = cls._scan_dir(entry_rel)
                    entries.append(
                        SkillFsTreeNode(key=entry_rel, label=name, isLeaf=False, children=children),
                    )
                elif os.path.isfile(entry_full):
                    entries.append(
                        SkillFsTreeNode(key=entry_rel, label=name, isLeaf=True, children=None),
                    )
            except ValueError:
                continue
        return cls._sort_nodes(entries)

    @classmethod
    def get_tree(cls) -> SkillFsTreeResponse:
        root = cls.get_root_path()
        exists = os.path.isdir(root)
        tree = cls._scan_dir('') if exists else []
        if not exists:
            logger.warning(f'Skills 目录不存在，将返回空树: {root}')
        return SkillFsTreeResponse(root_path=root, root_exists=exists, tree=tree)

    @classmethod
    def read_file(cls, rel_path: str) -> Tuple[bool, str, str]:
        if not rel_path or not rel_path.strip():
            return False, '路径不能为空', ''
        try:
            full = cls._safe_join(rel_path.strip())
        except ValueError:
            return False, '非法路径', ''
        if not os.path.isfile(full):
            return False, '不是文件或不存在', ''
        size = os.path.getsize(full)
        if size > _MAX_READ_BYTES:
            return False, f'文件过大（>{_MAX_READ_BYTES // 1024}KB），请在服务器上直接编辑', ''
        try:
            with open(full, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except OSError as e:
            return False, f'读取失败: {e}', ''
        return True, '', content

    @classmethod
    def extract_zip_to_root(cls, zip_path: str) -> Tuple[bool, str]:
        """将 ZIP 内容解压到 Skills 根目录（保持包内相对路径，如 my-skill/SKILL.md）。"""
        root = os.path.abspath(cls.get_root_path())
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            return False, f'无法创建或访问 Skills 根目录: {e}'
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for info in zf.infolist():
                    dest = os.path.abspath(os.path.join(root, info.filename))
                    if dest != root and not dest.startswith(root + os.sep):
                        raise ValueError('ZIP 内包含非法路径')
                zf.extractall(root)
        except zipfile.BadZipFile:
            return False, '无效的 ZIP 文件'
        except ValueError as e:
            return False, str(e) if str(e) else 'ZIP 路径非法'
        except OSError as e:
            return False, f'解压失败: {e}'
        return True, '解压成功'

def max_zip_bytes() -> int:
    return _MAX_ZIP_BYTES
