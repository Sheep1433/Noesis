"""当前会话上下文：按 sessions/{id}/ 目录树展示。"""

from __future__ import annotations

import os
from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config.user_data_paths import (
    get_session_root,
    get_session_uploads_dir,
    get_workspace_dir,
)
from schemas.session_context_vo import FsTreeNode, SessionContextResponse
from services.chat_service import ChatService
from services.skill_fs_service import SkillFsService

_MAX_READ_BYTES = 512 * 1024
_SESSION_PANEL_SUBDIRS = ('workspace', 'uploads')
_ALLOWED_READ_PREFIXES = ('workspace', 'uploads', 'attachments')


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
    async def get_context(
        cls,
        session_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> SessionContextResponse:
        await cls._ensure_owned(session_id, user_id, db)
        session_root = get_session_root(user_id, session_id)
        subdirs: List[FsTreeNode] = []
        for name in _SESSION_PANEL_SUBDIRS:
            if name == 'workspace':
                sub_path = get_workspace_dir(user_id, session_id)
            else:
                sub_path = get_session_uploads_dir(user_id, session_id)
            if not sub_path.is_dir():
                continue
            children = cls._scan_directory(str(sub_path), name)
            if not children:
                continue
            subdirs.append(
                FsTreeNode(
                    key=name,
                    label=name,
                    isLeaf=False,
                    children=children,
                ),
            )
        root_label = f'sessions/{session_id}'
        tree = [
            FsTreeNode(
                key=root_label,
                label=root_label,
                isLeaf=False,
                children=subdirs or None,
            ),
        ]
        return SessionContextResponse(
            tree=tree,
            session_root_path=str(session_root),
        )

    @classmethod
    def _normalize_session_rel_path(cls, rel_path: str) -> str:
        norm = rel_path.strip().replace('\\', '/')
        if not norm or '..' in norm.split('/'):
            raise HTTPException(status_code=400, detail='非法路径')
        if not norm.startswith(_ALLOWED_READ_PREFIXES):
            raise HTTPException(status_code=400, detail='非法路径')
        return norm

    @classmethod
    async def read_workspace_file(
        cls,
        session_id: str,
        user_id: str,
        rel_path: str,
        db: AsyncSession,
    ) -> tuple[str, str]:
        await cls._ensure_owned(session_id, user_id, db)
        rel_norm = cls._normalize_session_rel_path(rel_path)
        root = str(get_session_root(user_id, session_id))
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
