"""会话附件 file_dict 哨兵解析。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from utils.log_util import logger

CHAT_ATTACHMENT_REF = "__CHAT_ATTACHMENT__"
_INLINE_BODY_MIN_LEN = 800


def is_chat_attachment_ref(value: str) -> bool:
    return str(value).startswith(f"{CHAT_ATTACHMENT_REF}:")


def attachment_id_from_ref(value: str) -> Optional[str]:
    s = str(value).strip()
    prefix = f"{CHAT_ATTACHMENT_REF}:"
    if not s.startswith(prefix):
        return None
    aid = s[len(prefix):].strip()
    return aid or None


async def resolve_chat_attachments(
    file_dict: Optional[Dict[str, Any]],
    session_id: str,
    user_id: str,
    db: AsyncSession,
) -> List[str]:
    """
    将 file_dict 解析为 Markdown 片段列表（每项形如 ``### {file_name}\\n{body}``）。
    - 哨兵值 → 从存储读取正文（优先 markdown 副本）
    - 值长度 > 800 → 内联正文（兼容旧会话）
    - 其它 → warning 并跳过
    """
    from services.chat_attachment_service import ChatAttachmentService

    if not file_dict:
        return []

    parts: List[str] = []

    for name, val in file_dict.items():
        if val is None:
            continue
        file_name = str(name).strip()
        s = str(val).strip()
        if not file_name or not s:
            continue

        if is_chat_attachment_ref(s):
            aid = attachment_id_from_ref(s)
            if not aid:
                logger.warning(f"[resolve_chat_attachments] 无效哨兵 file_name={file_name}")
                continue
            body = await ChatAttachmentService.read_attachment_body(
                attachment_id=aid,
                session_id=session_id,
                user_id=user_id,
                db=db,
            )
            if body is None:
                logger.warning(
                    f"[resolve_chat_attachments] 附件不可读 attachment_id={aid} session_id={session_id}"
                )
                continue
            parts.append(f"### {file_name}\n{body}")
        elif len(s) > _INLINE_BODY_MIN_LEN:
            parts.append(f"### {file_name}\n{s}")
        else:
            logger.warning(
                f"[resolve_chat_attachments] 跳过无法解析的 file_dict 条目 file_name={file_name}"
            )

    return parts


async def resolve_chat_attachments_text(
    file_dict: Optional[Dict[str, Any]],
    session_id: str,
    user_id: str,
    db: AsyncSession,
) -> str:
    parts = await resolve_chat_attachments(file_dict, session_id, user_id, db)
    return "\n\n".join(parts)
