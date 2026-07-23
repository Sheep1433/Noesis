"""
Skills 文件目录（平台 extensions/skills + 用户 .data/users/{uid}/skills/）
"""
import io
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import List, Literal, Tuple

from config.extensions_paths import skills_root
from config.user_data_paths import ensure_user_skills_dir, get_user_skills_dir
from schemas.skill_vo import SkillFsSourceSection, SkillFsTreeNode, SkillFsTreeResponse
from common.logging import logger

_MAX_READ_BYTES = 512 * 1024
_MAX_ZIP_BYTES = 10 * 1024 * 1024
_REVISION_FILE = ".skills_revision"

SkillSource = Literal["platform", "user"]


def skills_revision_path(user_id: str) -> Path:
    return get_user_skills_dir(user_id) / _REVISION_FILE


def get_user_skills_revision(user_id: str) -> str:
    path = skills_revision_path(user_id)
    if not path.is_file():
        return "0"
    try:
        return path.read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


def bump_user_skills_revision(user_id: str) -> str:
    """个人 Skills 树变更后调用；返回新 revision。"""
    path = skills_revision_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = str(time.time_ns())
    path.write_text(value, encoding="utf-8")
    return value


class SkillFsService:
    """扫描平台与用户 Skills 目录，提供树形结构与安全读文件"""

    @classmethod
    def get_platform_root_path(cls) -> str:
        return str(skills_root())

    @classmethod
    def get_user_root_path(cls, user_id: str | int) -> str:
        return str(get_user_skills_dir(user_id))

    @classmethod
    def _safe_join(cls, root: str, rel: str) -> str:
        root = os.path.abspath(root)
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
    def _count_skill_packages(cls, tree: List[SkillFsTreeNode]) -> int:
        return sum(1 for node in tree if not node.isLeaf)

    @classmethod
    def _scan_dir(
        cls,
        root: str,
        rel: str,
        *,
        source: SkillSource,
        key_prefix: str,
    ) -> List[SkillFsTreeNode]:
        full = cls._safe_join(root, rel)
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
            node_key = f'{key_prefix}{entry_rel}'
            try:
                if source == 'user' and os.path.islink(entry_full):
                    continue
                if os.path.isdir(entry_full):
                    children = cls._scan_dir(
                        root, entry_rel, source=source, key_prefix=key_prefix,
                    )
                    entries.append(
                        SkillFsTreeNode(
                            key=node_key,
                            label=name,
                            isLeaf=False,
                            children=children,
                            source=source,
                        ),
                    )
                elif os.path.isfile(entry_full):
                    entries.append(
                        SkillFsTreeNode(
                            key=node_key,
                            label=name,
                            isLeaf=True,
                            children=None,
                            source=source,
                        ),
                    )
            except ValueError:
                continue
        return cls._sort_nodes(entries)

    @classmethod
    def _build_source_section(
        cls,
        root: str,
        *,
        source: SkillSource,
    ) -> SkillFsSourceSection:
        exists = os.path.isdir(root)
        key_prefix = f'{source}:'
        tree = cls._scan_dir(root, '', source=source, key_prefix=key_prefix) if exists else []
        if not exists:
            logger.warning(f'Skills 目录不存在，将返回空树: {root}')
        return SkillFsSourceSection(
            root_exists=exists,
            writable=source == 'user',
            skill_count=cls._count_skill_packages(tree),
            tree=tree,
        )

    @classmethod
    def get_tree(cls, user_id: str | int) -> SkillFsTreeResponse:
        platform_root = cls.get_platform_root_path()
        user_root = cls.get_user_root_path(user_id)
        platform = cls._build_source_section(platform_root, source='platform')
        user = cls._build_source_section(user_root, source='user')

        merged: List[SkillFsTreeNode] = []
        if platform.tree or platform.root_exists:
            merged.append(
                SkillFsTreeNode(
                    key='platform:',
                    label='平台预置',
                    isLeaf=False,
                    children=platform.tree,
                    source='platform',
                ),
            )
        merged.append(
            SkillFsTreeNode(
                key='user:',
                label='个人技能',
                isLeaf=False,
                children=user.tree,
                source='user',
            ),
        )
        return SkillFsTreeResponse(
            platform=platform,
            user=user,
            tree=merged,
        )

    @classmethod
    def _resolve_root(cls, source: SkillSource, user_id: str | int) -> str:
        if source == 'platform':
            return cls.get_platform_root_path()
        return str(ensure_user_skills_dir(user_id))

    @classmethod
    def read_file(
        cls,
        rel_path: str,
        *,
        source: SkillSource = 'platform',
        user_id: str | int | None = None,
    ) -> Tuple[bool, str, str]:
        if not rel_path or not rel_path.strip():
            return False, '路径不能为空', ''
        rel = rel_path.strip()
        if rel.startswith('platform:'):
            rel = rel[len('platform:'):]
            source = 'platform'
        elif rel.startswith('user:'):
            rel = rel[len('user:'):]
            source = 'user'
        if source == 'user' and user_id is None:
            return False, '缺少用户上下文', ''
        try:
            root = cls._resolve_root(source, user_id or '')
            full = cls._safe_join(root, rel)
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
    def extract_zip_to_user_dir(cls, zip_path: str, user_id: str | int) -> Tuple[bool, str]:
        """将 ZIP 内容解压到 `.data/users/{user_id}/skills/`。"""
        root = str(ensure_user_skills_dir(user_id))
        ok, msg = cls._extract_zip(zip_path, root)
        if ok:
            bump_user_skills_revision(str(user_id))
        return ok, msg

    @classmethod
    def _extract_zip(cls, zip_path: str, root: str) -> Tuple[bool, str]:
        root = os.path.abspath(root)
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            return False, f'无法创建或访问 Skills 目录: {e}'
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

    @classmethod
    def _is_top_level_package_name(cls, package_name: str) -> bool:
        name = package_name.strip().replace('\\', '/')
        if not name or '/' in name or name in ('.', '..') or name.startswith('.'):
            return False
        return True

    @classmethod
    def user_package_exists(cls, package_name: str, user_id: str | int) -> bool:
        if not cls._is_top_level_package_name(package_name):
            return False
        name = package_name.strip().replace('\\', '/')
        try:
            root = str(ensure_user_skills_dir(user_id))
            target = cls._safe_join(root, name)
        except ValueError:
            return False
        return os.path.isdir(target) and not os.path.islink(target)

    @classmethod
    def install_package_dir(
        cls,
        src_dir: str,
        package_name: str,
        user_id: str | int,
        *,
        overwrite: bool = False,
    ) -> Tuple[bool, str]:
        """将本地技能目录复制到用户 personal skills（顶层包名）。"""
        if not cls._is_top_level_package_name(package_name):
            return False, '非法技能包名'
        name = package_name.strip().replace('\\', '/')
        src = os.path.abspath(src_dir)
        if not os.path.isdir(src):
            return False, '源技能目录不存在'
        if not os.path.isfile(os.path.join(src, 'SKILL.md')):
            return False, '源目录缺少 SKILL.md'
        try:
            root = str(ensure_user_skills_dir(user_id))
            dest = cls._safe_join(root, name)
        except ValueError:
            return False, '非法路径'
        if os.path.abspath(dest) == os.path.abspath(root):
            return False, '不能安装到根目录'
        if os.path.lexists(dest):
            if not overwrite:
                return False, f'技能「{name}」已存在'
            if os.path.islink(dest) or not os.path.isdir(dest):
                return False, '目标路径非法，无法覆盖'
            try:
                shutil.rmtree(dest)
            except OSError as e:
                return False, f'覆盖前删除失败: {e}'
        try:
            shutil.copytree(
                src,
                dest,
                ignore=shutil.ignore_patterns('.git', '.github', '__pycache__', 'node_modules'),
                symlinks=False,
            )
        except OSError as e:
            logger.error(f'安装技能目录失败 {src} -> {dest}: {e}')
            return False, f'安装失败: {e}'
        bump_user_skills_revision(str(user_id))
        return True, f'已安装技能「{name}」'

    @classmethod
    def delete_user_skill_package(
        cls,
        package_name: str,
        user_id: str | int,
    ) -> Tuple[bool, str]:
        """删除当前用户个人技能库下的顶层技能目录（仅单段目录名）。"""
        if not cls._is_top_level_package_name(package_name):
            return False, '只能删除个人技能下的顶层技能目录'
        name = package_name.strip().replace('\\', '/')
        try:
            root = str(ensure_user_skills_dir(user_id))
            target = cls._safe_join(root, name)
        except ValueError:
            return False, '非法路径'
        if os.path.abspath(target) == os.path.abspath(root):
            return False, '不能删除根目录'
        if not os.path.isdir(target):
            return False, '技能目录不存在'
        if os.path.islink(target):
            return False, '不能删除符号链接'
        try:
            shutil.rmtree(target)
        except OSError as e:
            logger.error(f'删除技能目录失败 {target}: {e}')
            return False, f'删除失败: {e}'
        bump_user_skills_revision(str(user_id))
        return True, f'已删除技能「{name}」'

    @classmethod
    def build_package_zip(
        cls,
        package_name: str,
        *,
        source: SkillSource,
        user_id: str | int,
    ) -> Tuple[bool, str, bytes, str]:
        """将顶层技能目录打包为 ZIP（平台/个人均可下载）。"""
        if not cls._is_top_level_package_name(package_name):
            return False, '仅支持下载顶层技能目录', b'', ''
        name = package_name.strip().replace('\\', '/')
        try:
            root = cls._resolve_root(source, user_id)
            package_dir = cls._safe_join(root, name)
        except ValueError:
            return False, '非法路径', b'', ''
        if not os.path.isdir(package_dir):
            return False, '技能目录不存在', b'', ''
        if os.path.islink(package_dir):
            return False, '不能打包符号链接目录', b'', ''
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(package_dir):
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                for fn in filenames:
                    if fn.startswith('.'):
                        continue
                    full = os.path.join(dirpath, fn)
                    if os.path.islink(full):
                        continue
                    arcname = os.path.relpath(full, package_dir).replace('\\', '/')
                    zf.write(full, arcname)
        data = buf.getvalue()
        if len(data) > _MAX_ZIP_BYTES:
            return False, f'技能包过大（>{_MAX_ZIP_BYTES // 1024 // 1024}MB）', b'', ''
        return True, '', data, f'{name}.zip'


def max_zip_bytes() -> int:
    return _MAX_ZIP_BYTES
