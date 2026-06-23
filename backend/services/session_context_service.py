"""当前会话上下文：工作区文件树 + 附件列表。"""

from __future__ import annotations

import os
from typing import List

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config.user_data_paths import get_workspace_dir
from schemas.chat_attachment_vo import AttachmentResponse
from schemas.session_context_vo import FsTreeNode, SessionContextResponse
from services.chat_attachment_service import ChatAttachmentService
from services.chat_service import ChatService
from services.skill_fs_service import SkillFsService

_MAX_READ_BYTES = 512 * 1024


class SessionContextService:
    @classmethod
    async def _ensure_owned(cls, session_id: str, user_id: str, db: AsyncSession) -> None:
        session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

    @classmethod
    def _scan_workspace(cls, root: str, rel: str) -> List[FsTreeNode]:
        full = SkillFsService._safe_join(root, rel)
        if not os.path.isdir(full):
            return []
        entries: List[FsTreeNode] = []
        try:
            names = sorted(os.listdir(full), key=lambda x: x.lower())
        except OSError:
            return []
        for name in names:
            if name.startswith('.'):
                continue
            entry_rel = f'{rel}/{name}' if rel else name
            entry_full = os.path.join(full, name)
            try:
                if os.path.isdir(entry_full):
                    children = cls._scan_workspace(root, entry_rel)
                    entries.append(
                        FsTreeNode(
                            key=entry_rel,
                            label=name,
                            isLeaf=False,
                            children=children or None,
                        ),
                    )
                elif os.path.isfile(entry_full):
                    entries.append(
                        FsTreeNode(key=entry_rel, label=name, isLeaf=True, children=None),
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
        ws_dir = get_workspace_dir(user_id, session_id)
        ws_exists = ws_dir.is_dir()
        workspace_tree = cls._scan_workspace(str(ws_dir), '') if ws_exists else []
        attachments: List[AttachmentResponse] = await ChatAttachmentService.list_attachments(
            session_id=session_id,
            user_id=user_id,
            db=db,
        )
        return SessionContextResponse(
            workspace=workspace_tree,
            attachments=attachments,
            workspace_root_exists=ws_exists,
            workspace_root_path=str(ws_dir),
        )

    @classmethod
    async def read_workspace_file(
        cls,
        session_id: str,
        user_id: str,
        rel_path: str,
        db: AsyncSession,
    ) -> tuple[str, str]:
        await cls._ensure_owned(session_id, user_id, db)
        if not rel_path or not rel_path.strip():
            raise HTTPException(status_code=400, detail="路径不能为空")
        root = str(get_workspace_dir(user_id, session_id))
        try:
            full = SkillFsService._safe_join(root, rel_path.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="非法路径")
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
                return rel_path.strip(), handle.read()
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"读取失败: {exc}") from exc
