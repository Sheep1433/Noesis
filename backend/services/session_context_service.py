"""当前会话上下文：按 users/{uid}/ 目录树展示，sessions 仅含当前会话。"""

from __future__ import annotations

import io
import mimetypes
import os
import sys
import time
import zipfile
from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config.user_data_paths import (
    get_session_root,
    get_session_uploads_dir,
    get_user_agents_md_path,
    get_user_profile_md_path,
    get_user_root,
    get_user_skills_dir,
    get_workspace_dir,
)
from schemas.session_context_vo import FsTreeNode, SessionContextResponse
from services.chat_service import ChatService
from services.skill_fs_service import SkillFsService

_MAX_READ_BYTES = 512 * 1024
_MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
_MIN_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_USER_ROOT_FILES = ('AGENTS.md', 'USER.md')
_SESSION_PANEL_SUBDIRS = ('workspace', 'uploads')


class SessionContextService:
    @classmethod
    async def _ensure_owned(cls, session_id: str, user_id: str, db: AsyncSession) -> None:
        session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

    @classmethod
    def _scan_directory(cls, dir_path: str, key_prefix: str) -> List[FsTreeNode]:
        if not os.path.isdir(dir_path):
            return []
        entries: List[FsTreeNode] = []
        try:
            names = sorted(os.listdir(dir_path), key=lambda x: x.lower())
        except OSError:
            return []
        for name in names:
            if name.startswith('.'):
                continue
            entry_key = f'{key_prefix}/{name}'
            entry_full = os.path.join(dir_path, name)
            try:
                if os.path.isdir(entry_full):
                    children = cls._scan_directory(entry_full, entry_key)
                    entries.append(
                        FsTreeNode(
                            key=entry_key,
                            label=name,
                            isLeaf=False,
                            children=children or None,
                        ),
                    )
                elif os.path.isfile(entry_full):
                    entries.append(
                        FsTreeNode(key=entry_key, label=name, isLeaf=True, children=None),
                    )
            except ValueError:
                continue
        return sorted(entries, key=lambda n: (n.isLeaf, n.label.lower()))

    @classmethod
    def _user_root_file_nodes(cls, user_id: str) -> List[FsTreeNode]:
        entries: List[FsTreeNode] = []
        for path in (get_user_agents_md_path(user_id), get_user_profile_md_path(user_id)):
            if path.is_file():
                entries.append(
                    FsTreeNode(key=path.name, label=path.name, isLeaf=True, children=None),
                )
        return entries

    @classmethod
    def _current_session_nodes(cls, user_id: str, session_id: str) -> List[FsTreeNode]:
        subdirs: List[FsTreeNode] = []
        session_key = f'sessions/{session_id}'
        for name in _SESSION_PANEL_SUBDIRS:
            if name == 'workspace':
                sub_path = get_workspace_dir(user_id, session_id)
            else:
                sub_path = get_session_uploads_dir(user_id, session_id)
            if not sub_path.is_dir():
                continue
            children = cls._scan_directory(str(sub_path), f'{session_key}/{name}')
            if not children:
                continue
            subdirs.append(
                FsTreeNode(
                    key=f'{session_key}/{name}',
                    label=name,
                    isLeaf=False,
                    children=children,
                ),
            )
        if not subdirs:
            return []
        return [
            FsTreeNode(
                key=session_key,
                label=session_key,
                isLeaf=False,
                children=subdirs,
            ),
        ]

    @classmethod
    async def get_context(
        cls,
        session_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> SessionContextResponse:
        await cls._ensure_owned(session_id, user_id, db)
        user_root = get_user_root(user_id)
        children: List[FsTreeNode] = []
        children.extend(cls._user_root_file_nodes(user_id))
        skills_dir = get_user_skills_dir(user_id)
        if skills_dir.is_dir():
            skills_children = cls._scan_directory(str(skills_dir), 'skills')
            if skills_children:
                children.append(
                    FsTreeNode(
                        key='skills',
                        label='skills',
                        isLeaf=False,
                        children=skills_children,
                    ),
                )
        children.extend(cls._current_session_nodes(user_id, session_id))
        root_label = f'users/{user_id}'
        tree = [
            FsTreeNode(
                key=root_label,
                label=root_label,
                isLeaf=False,
                children=children or None,
            ),
        ]
        return SessionContextResponse(
            tree=tree,
            session_root_path=str(get_session_root(user_id, session_id)),
        )

    @classmethod
    def _normalize_rel_path(cls, rel_path: str, session_id: str) -> str:
        norm = rel_path.strip().replace('\\', '/')
        if not norm or '..' in norm.split('/'):
            raise HTTPException(status_code=400, detail='非法路径')
        if norm in _USER_ROOT_FILES:
            return norm
        if norm.startswith('skills/'):
            return norm
        session_prefix = f'sessions/{session_id}/'
        if norm.startswith(session_prefix):
            tail = norm[len(session_prefix):]
            if tail.startswith(('workspace/', 'uploads/', 'attachments/')):
                return norm
        raise HTTPException(status_code=400, detail='非法路径')

    @classmethod
    def _normalize_archive_path(cls, rel_path: str, session_id: str) -> str:
        norm = rel_path.strip().replace('\\', '/').rstrip('/')
        if not norm or '..' in norm.split('/'):
            raise HTTPException(status_code=400, detail='非法路径')
        if norm.startswith('users/'):
            raise HTTPException(status_code=400, detail='非法路径')
        if norm in _USER_ROOT_FILES:
            return norm
        if norm == 'skills' or norm.startswith('skills/'):
            return norm
        session_prefix = f'sessions/{session_id}'
        if norm == session_prefix:
            return norm
        if norm in (f'{session_prefix}/workspace', f'{session_prefix}/uploads'):
            return norm
        session_prefix_slash = f'{session_prefix}/'
        if norm.startswith(session_prefix_slash):
            tail = norm[len(session_prefix_slash):]
            if tail.startswith(('workspace/', 'uploads/', 'attachments/')):
                return norm
            if tail in ('workspace', 'uploads', 'attachments'):
                return norm
        raise HTTPException(status_code=400, detail='非法路径')

    @classmethod
    def _archive_download_name(cls, rel_norm: str) -> str:
        base = rel_norm.rstrip('/').split('/')[-1] or 'archive'
        return base if base.lower().endswith('.zip') else f'{base}.zip'

    @classmethod
    def _safe_zip_date_time(cls, path: str) -> tuple[int, int, int, int, int, int]:
        st = os.stat(path)
        dt = time.localtime(st.st_mtime)
        current = (dt.tm_year, dt.tm_mon, dt.tm_mday, dt.tm_hour, dt.tm_min, dt.tm_sec)
        if current < _MIN_ZIP_DATE_TIME:
            return _MIN_ZIP_DATE_TIME
        return current

    @classmethod
    def _write_file_to_zip(cls, zf: zipfile.ZipFile, full_path: str, arcname: str) -> None:
        info = zipfile.ZipInfo(
            arcname.replace('\\', '/'),
            date_time=cls._safe_zip_date_time(full_path),
        )
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(full_path, 'rb') as handle:
            zf.writestr(info, handle.read())

    @classmethod
    def _add_path_to_zip(
        cls,
        zf: zipfile.ZipFile,
        full_path: str,
        arc_root: str,
        total_bytes: int,
    ) -> int:
        if os.path.isfile(full_path):
            size = os.path.getsize(full_path)
            total_bytes += size
            if total_bytes > _MAX_ARCHIVE_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"打包内容过大（>{_MAX_ARCHIVE_BYTES // (1024 * 1024)}MB）",
                )
            cls._write_file_to_zip(zf, full_path, os.path.basename(full_path))
            return total_bytes

        if not os.path.isdir(full_path):
            raise HTTPException(status_code=404, detail="路径不存在")

        for dirpath, dirnames, filenames in os.walk(full_path):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith('.') and not os.path.islink(os.path.join(dirpath, d))
            )
            for fname in sorted(filenames):
                if fname.startswith('.'):
                    continue
                fpath = os.path.join(dirpath, fname)
                if os.path.islink(fpath):
                    continue
                if not os.path.isfile(fpath):
                    continue
                size = os.path.getsize(fpath)
                total_bytes += size
                if total_bytes > _MAX_ARCHIVE_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"打包内容过大（>{_MAX_ARCHIVE_BYTES // (1024 * 1024)}MB）",
                    )
                rel_arc = os.path.relpath(fpath, arc_root)
                cls._write_file_to_zip(zf, fpath, rel_arc)
        return total_bytes

    @classmethod
    def _build_zip_bytes(cls, full_path: str, rel_norm: str) -> tuple[str, bytes]:
        buffer = io.BytesIO()
        zip_kwargs: dict = {}
        if sys.version_info >= (3, 12):
            zip_kwargs['strict_timestamps'] = False
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, **zip_kwargs) as zf:
            cls._add_path_to_zip(zf, full_path, full_path, 0)
            if not zf.namelist():
                raise HTTPException(status_code=400, detail='目录为空，无法下载')
        return cls._archive_download_name(rel_norm), buffer.getvalue()

    @classmethod
    async def download_workspace_path(
        cls,
        session_id: str,
        user_id: str,
        rel_path: str,
        db: AsyncSession,
    ) -> tuple[str, bytes, str]:
        await cls._ensure_owned(session_id, user_id, db)
        rel_norm = cls._normalize_archive_path(rel_path, session_id)
        root = str(get_user_root(user_id))
        try:
            full = SkillFsService._safe_join(root, rel_norm)
        except ValueError:
            raise HTTPException(status_code=400, detail='非法路径')
        if not os.path.exists(full):
            raise HTTPException(status_code=404, detail="路径不存在")

        if os.path.isfile(full):
            size = os.path.getsize(full)
            if size > _MAX_ARCHIVE_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"文件过大（>{_MAX_ARCHIVE_BYTES // (1024 * 1024)}MB）",
                )
            try:
                with open(full, 'rb') as handle:
                    data = handle.read()
            except OSError as exc:
                raise HTTPException(status_code=400, detail=f"读取失败: {exc}") from exc
            name = os.path.basename(full)
            media_type, _ = mimetypes.guess_type(name)
            return name, data, media_type or 'application/octet-stream'

        name, data = cls._build_zip_bytes(full, rel_norm)
        return name, data, 'application/zip'

    @classmethod
    async def build_path_archive(
        cls,
        session_id: str,
        user_id: str,
        rel_path: str,
        db: AsyncSession,
    ) -> tuple[str, bytes]:
        name, data, media_type = await cls.download_workspace_path(
            session_id, user_id, rel_path, db,
        )
        if media_type != 'application/zip':
            raise HTTPException(status_code=400, detail='请对目录使用打包下载')
        return name, data

    @classmethod
    async def read_workspace_file(
        cls,
        session_id: str,
        user_id: str,
        rel_path: str,
        db: AsyncSession,
    ) -> tuple[str, str]:
        await cls._ensure_owned(session_id, user_id, db)
        rel_norm = cls._normalize_rel_path(rel_path, session_id)
        root = str(get_user_root(user_id))
        try:
            full = SkillFsService._safe_join(root, rel_norm)
        except ValueError:
            raise HTTPException(status_code=400, detail='非法路径')
        if not os.path.isfile(full):
            raise HTTPException(status_code=404, detail="不是文件或不存在")
        size = os.path.getsize(full)
        if size > _MAX_READ_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"文件过大（>{_MAX_READ_BYTES // 1024}KB）",
            )
        try:
            with open(full, 'r', encoding='utf-8', errors='replace') as handle:
                return rel_norm, handle.read()
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"读取失败: {exc}") from exc

    @classmethod
    def _validate_write_size(cls, content: str) -> None:
        if len(content.encode('utf-8')) > _MAX_READ_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"文件过大（>{_MAX_READ_BYTES // 1024}KB）",
            )

    @classmethod
    async def write_workspace_file(
        cls,
        session_id: str,
        user_id: str,
        rel_path: str,
        content: str,
        db: AsyncSession,
    ) -> tuple[str, str]:
        await cls._ensure_owned(session_id, user_id, db)
        rel_norm = cls._normalize_rel_path(rel_path, session_id)
        root = str(get_user_root(user_id))
        try:
            full = SkillFsService._safe_join(root, rel_norm)
        except ValueError:
            raise HTTPException(status_code=400, detail='非法路径')
        if not os.path.isfile(full):
            raise HTTPException(status_code=404, detail="不是文件或不存在")
        cls._validate_write_size(content)
        try:
            with open(full, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(content)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"写入失败: {exc}") from exc
        return rel_norm, content
