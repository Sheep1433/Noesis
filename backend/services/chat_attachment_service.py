"""聊天会话附件：磁盘存储 + MySQL 元数据。"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import ChatAttachmentConfig
from config.user_data_paths import (
    ensure_session_attachments_dir,
    ensure_session_uploads_dir,
    get_session_root,
)
from exceptions.exception import ServiceWarning
from kb.document_parse import DocumentParser
from models.chat_models import TChatAttachment
from schemas.chat_attachment_vo import AttachmentResponse
from services.chat_service import ChatService
from common.logging import logger
from domain.chat.attachments.markdown import extract_preview

_DOCUMENT_EXTENSIONS = frozenset({
    ".doc", ".docx", ".pdf", ".txt", ".xlsx", ".csv", ".ppt", ".pptx", ".md",
})
_IMAGE_MIME_TYPES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/gif",
})
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|]')
_PREVIEW_IMAGE_MAX_BYTES = 200 * 1024


def _now_ms() -> int:
    return int(time.time() * 1000)


def _expires_at_ms() -> int:
    return _now_ms() + ChatAttachmentConfig.ttl_days * 24 * 3600 * 1000


def _sanitize_filename(name: str) -> str:
    base = os.path.basename(name or "file").strip()
    base = _UNSAFE_NAME_RE.sub("_", base)
    return base or "file"


def _session_dir(user_id: str, session_id: str) -> Path:
    return get_session_root(user_id, session_id)


def _uploads_dir(user_id: str, session_id: str) -> Path:
    return ensure_session_uploads_dir(user_id, session_id)


def _attachments_dir(user_id: str, session_id: str) -> Path:
    return ensure_session_attachments_dir(user_id, session_id)


def _rel_path(absolute: Path, user_id: str, session_id: str) -> str:
    session_root = _session_dir(user_id, session_id).resolve()
    return str(absolute.resolve().relative_to(session_root))


def _abs_path(relative: str, user_id: str, session_id: str) -> Path:
    rel = relative.replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("非法路径")

    session_root = _session_dir(user_id, session_id).resolve()
    candidate = (session_root / rel).resolve()
    if not str(candidate).startswith(str(session_root)):
        raise ValueError("非法路径")
    return candidate


def _detect_kind(filename: str, mime_type: Optional[str]) -> Optional[str]:
    ext = os.path.splitext(filename)[1].lower()
    if mime_type and mime_type in _IMAGE_MIME_TYPES:
        return "image"
    if ext in _DOCUMENT_EXTENSIONS:
        return "document"
    guessed, _ = mimetypes.guess_type(filename)
    if guessed in _IMAGE_MIME_TYPES:
        return "image"
    return None


def _artifact_url(session_id: str, relative_path: str) -> str:
    return f"/api/chat/sessions/{session_id}/artifacts/{relative_path}"


class ChatAttachmentService:

    @classmethod
    def _to_response(cls, row: TChatAttachment, preview: Optional[str] = None) -> AttachmentResponse:
        rel = row.original_path
        return AttachmentResponse(
            attachment_id=row.id,
            file_name=row.file_name,
            kind=row.kind,
            mime_type=row.mime_type,
            status=row.status,
            char_count=row.char_count,
            preview=preview,
            virtual_path=row.virtual_path,
            artifact_url=_artifact_url(row.session_id, rel),
        )

    @classmethod
    def _is_expired(cls, row: TChatAttachment) -> bool:
        return row.expires_at <= _now_ms()

    @classmethod
    async def _ensure_session_owned(
        cls, session_id: str, user_id: str, db: AsyncSession
    ):
        session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

    @classmethod
    async def _count_active(cls, session_id: str, db: AsyncSession) -> int:
        now = _now_ms()
        result = await db.execute(
            select(TChatAttachment).where(
                and_(
                    TChatAttachment.session_id == session_id,
                    TChatAttachment.expires_at > now,
                )
            )
        )
        return len(result.scalars().all())

    @classmethod
    async def lazy_delete_expired(cls, session_id: str, db: AsyncSession) -> int:
        now = _now_ms()
        result = await db.execute(
            select(TChatAttachment).where(
                and_(
                    TChatAttachment.session_id == session_id,
                    TChatAttachment.expires_at <= now,
                )
            )
        )
        rows = list(result.scalars().all())
        deleted = 0
        for row in rows:
            cls._delete_disk_files(row)
            await db.execute(delete(TChatAttachment).where(TChatAttachment.id == row.id))
            deleted += 1
        if deleted:
            await db.commit()
            logger.info(f"lazy delete 过期附件 session_id={session_id} count={deleted}")
        return deleted

    @classmethod
    def _delete_disk_files(cls, row: TChatAttachment) -> None:
        for rel in (row.original_path, row.markdown_path):
            if not rel:
                continue
            try:
                path = _abs_path(rel, row.user_id, row.session_id)
            except ValueError:
                continue
            try:
                if path.is_file():
                    path.unlink()
            except OSError as exc:
                logger.warning(f"删除附件文件失败 path={path}: {exc}")

    @classmethod
    async def get_by_id(
        cls,
        attachment_id: str,
        session_id: str,
        user_id: str,
        db: AsyncSession,
        *,
        allow_expired: bool = False,
    ) -> Optional[TChatAttachment]:
        result = await db.execute(
            select(TChatAttachment).where(
                and_(
                    TChatAttachment.id == attachment_id,
                    TChatAttachment.session_id == session_id,
                    TChatAttachment.user_id == user_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        if not allow_expired and cls._is_expired(row):
            return None
        return row

    @classmethod
    async def read_attachment_body(
        cls,
        attachment_id: str,
        session_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> Optional[str]:
        row = await cls.get_by_id(attachment_id, session_id, user_id, db)
        if not row:
            return None
        if row.kind == "image":
            return f"（图片附件 {row.file_name}，请通过 multimodal 内容查看）"
        text, _ = cls._read_document_text(row)
        return text

    @classmethod
    def _read_document_text(cls, row: TChatAttachment) -> Tuple[str, Path]:
        uid, sid = row.user_id, row.session_id
        if row.markdown_path:
            path = _abs_path(row.markdown_path, uid, sid)
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace"), path
        path = _abs_path(row.original_path, uid, sid)
        if not path.is_file():
            return "", path
        ext = path.suffix.lower()
        if ext in _DOCUMENT_EXTENSIONS:
            text = DocumentParser.convert_file_to_markdown(str(path))
            return text or "", path
        return path.read_text(encoding="utf-8", errors="replace"), path

    @classmethod
    def read_image_bytes(cls, row: TChatAttachment) -> Tuple[bytes, str]:
        path = _abs_path(row.original_path, row.user_id, row.session_id)
        if not path.is_file():
            raise FileNotFoundError(row.original_path)
        mime = row.mime_type or mimetypes.guess_type(row.file_name)[0] or "application/octet-stream"
        return path.read_bytes(), mime

    @classmethod
    async def upload(
        cls,
        session_id: str,
        user_id: str,
        filename: str,
        content: bytes,
        mime_type: Optional[str],
        db: AsyncSession,
    ) -> AttachmentResponse:
        if not ChatAttachmentConfig.enabled:
            raise ServiceWarning(message="会话附件功能已关闭")

        await cls._ensure_session_owned(session_id, user_id, db)
        await cls.lazy_delete_expired(session_id, db)

        safe_name = _sanitize_filename(filename)
        kind = _detect_kind(safe_name, mime_type)
        if not kind:
            raise ServiceWarning(message=f"不支持的文件格式: {safe_name}")

        size_mb = len(content) / (1024 * 1024)
        if kind == "document" and size_mb > ChatAttachmentConfig.max_file_mb:
            raise ServiceWarning(message=f"文档大小超过 {ChatAttachmentConfig.max_file_mb}MB 限制")
        if kind == "image" and size_mb > ChatAttachmentConfig.max_image_mb:
            raise ServiceWarning(message=f"图片大小超过 {ChatAttachmentConfig.max_image_mb}MB 限制")

        active_count = await cls._count_active(session_id, db)
        if active_count >= ChatAttachmentConfig.max_count_per_session:
            raise ServiceWarning(
                message=f"每会话最多 {ChatAttachmentConfig.max_count_per_session} 个附件"
            )

        attachment_id = str(uuid.uuid4())
        upload_path = _uploads_dir(user_id, session_id) / safe_name
        upload_path.write_bytes(content)
        original_rel = _rel_path(upload_path, user_id, session_id)

        resolved_mime = mime_type or mimetypes.guess_type(safe_name)[0]
        char_count = 0
        markdown_rel: Optional[str] = None
        status = "uploaded"
        parse_error: Optional[str] = None
        preview: Optional[str] = None
        preview_base64: Optional[str] = None

        if kind == "document":
            stem = Path(safe_name).stem
            virtual_path = f"/sessions/{session_id}/attachments/{stem}.md"
            if ChatAttachmentConfig.auto_convert:
                if len(content) > 1024 * 1024:
                    md_text = await asyncio.to_thread(
                        DocumentParser.convert_file_to_markdown, str(upload_path)
                    )
                else:
                    md_text = DocumentParser.convert_file_to_markdown(str(upload_path))
                md_text = (md_text or "").strip()
                if not md_text:
                    cls._delete_disk_files_from_paths(
                        user_id, session_id, original_rel, None
                    )
                    raise HTTPException(
                        status_code=422,
                        detail="文档解析后正文为空，请检查文件是否为扫描件或格式损坏",
                    )
                md_path = _attachments_dir(user_id, session_id) / f"{stem}.md"
                md_path.write_text(md_text, encoding="utf-8")
                markdown_rel = _rel_path(md_path, user_id, session_id)
                char_count = len(md_text)
                status = "parsed"
                preview = extract_preview(md_text, ChatAttachmentConfig.preview_chars)
            else:
                preview = f"（已上传 {safe_name}，未自动解析）"
        else:
            virtual_path = f"/sessions/{session_id}/uploads/{safe_name}"
            preview = f"图片 {safe_name}"
            if len(content) <= _PREVIEW_IMAGE_MAX_BYTES:
                preview_base64 = base64.b64encode(content).decode("ascii")

        now = _now_ms()
        row = TChatAttachment(
            id=attachment_id,
            session_id=session_id,
            user_id=user_id,
            file_name=safe_name,
            kind=kind,
            original_path=original_rel,
            markdown_path=markdown_rel,
            mime_type=resolved_mime,
            virtual_path=virtual_path,
            char_count=char_count,
            status=status,
            preview_base64=preview_base64,
            created_at=now,
            expires_at=_expires_at_ms(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        resp = cls._to_response(row, preview=preview)
        if parse_error:
            resp.parse_error = parse_error
        return resp

    @classmethod
    def _delete_disk_files_from_paths(
        cls,
        user_id: str,
        session_id: str,
        original_rel: str,
        markdown_rel: Optional[str],
    ) -> None:
        for rel in (original_rel, markdown_rel):
            if not rel:
                continue
            try:
                path = _abs_path(rel, user_id, session_id)
            except ValueError:
                continue
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass

    @classmethod
    async def list_attachments(
        cls,
        session_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> List[AttachmentResponse]:
        await cls._ensure_session_owned(session_id, user_id, db)
        await cls.lazy_delete_expired(session_id, db)

        now = _now_ms()
        result = await db.execute(
            select(TChatAttachment)
            .where(
                and_(
                    TChatAttachment.session_id == session_id,
                    TChatAttachment.user_id == user_id,
                    TChatAttachment.expires_at > now,
                )
            )
            .order_by(TChatAttachment.created_at.desc())
        )
        rows = list(result.scalars().all())
        items: List[AttachmentResponse] = []
        for row in rows:
            preview = None
            if row.kind == "document" and row.status == "parsed":
                text, _ = cls._read_document_text(row)
                preview = extract_preview(text, ChatAttachmentConfig.preview_chars)
            elif row.kind == "image":
                preview = f"图片 {row.file_name}"
            items.append(cls._to_response(row, preview=preview))
        return items

    @classmethod
    async def delete_attachment(
        cls,
        session_id: str,
        attachment_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> None:
        await cls._ensure_session_owned(session_id, user_id, db)
        row = await cls.get_by_id(
            attachment_id, session_id, user_id, db, allow_expired=True
        )
        if not row:
            raise HTTPException(status_code=404, detail="附件不存在")
        cls._delete_disk_files(row)
        await db.execute(delete(TChatAttachment).where(TChatAttachment.id == attachment_id))
        await db.commit()

    @classmethod
    async def get_artifact_file(
        cls,
        session_id: str,
        user_id: str,
        relative_path: str,
        db: AsyncSession,
    ) -> Tuple[Path, str]:
        await cls._ensure_session_owned(session_id, user_id, db)
        if ".." in relative_path or relative_path.startswith("/"):
            raise HTTPException(status_code=400, detail="非法路径")

        abs_path = _abs_path(relative_path, user_id, session_id)
        if not abs_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        norm = relative_path.replace("\\", "/")
        if not norm.startswith(("uploads/", "attachments/")):
            raise HTTPException(status_code=403, detail="无权访问该文件")

        session_root = _session_dir(user_id, session_id).resolve()
        if not str(abs_path.resolve()).startswith(str(session_root)):
            raise HTTPException(status_code=403, detail="无权访问该文件")

        mime = mimetypes.guess_type(abs_path.name)[0] or "application/octet-stream"
        return abs_path, mime

    @classmethod
    async def list_session_documents(
        cls,
        session_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> List[TChatAttachment]:
        now = _now_ms()
        result = await db.execute(
            select(TChatAttachment).where(
                and_(
                    TChatAttachment.session_id == session_id,
                    TChatAttachment.user_id == user_id,
                    TChatAttachment.kind == "document",
                    TChatAttachment.expires_at > now,
                )
            )
            .order_by(TChatAttachment.created_at.asc())
        )
        return list(result.scalars().all())

    @classmethod
    async def find_document(
        cls,
        *,
        session_id: str,
        user_id: str,
        path: str,
        db: AsyncSession,
    ) -> Optional[TChatAttachment]:
        needle = (path or "").strip()
        if not needle:
            return None
        rows = await cls.list_session_documents(session_id, user_id, db)
        for row in rows:
            if row.virtual_path == needle or row.file_name == needle:
                return row
        basename = os.path.basename(needle)
        for row in rows:
            if row.file_name == basename:
                return row
        return None

    @classmethod
    async def session_has_attachments(
        cls,
        session_id: str,
        user_id: str,
        db: AsyncSession,
        file_dict: Optional[dict] = None,
    ) -> bool:
        if file_dict:
            return True
        docs = await cls.list_session_documents(session_id, user_id, db)
        if docs:
            return True
        images = await cls.list_session_images(session_id, user_id, db)
        return bool(images)

    @classmethod
    async def list_session_images(
        cls,
        session_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> List[TChatAttachment]:
        now = _now_ms()
        result = await db.execute(
            select(TChatAttachment).where(
                and_(
                    TChatAttachment.session_id == session_id,
                    TChatAttachment.user_id == user_id,
                    TChatAttachment.kind == "image",
                    TChatAttachment.expires_at > now,
                )
            )
            .order_by(TChatAttachment.created_at.asc())
        )
        return list(result.scalars().all())
