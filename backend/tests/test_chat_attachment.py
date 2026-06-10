"""会话附件：outline、哨兵解析与 kind 检测。"""

from unittest.mock import AsyncMock, patch

import pytest

from services.chat_attachment_service import ChatAttachmentService
from utils.attachment_tool import (
    CHAT_ATTACHMENT_REF,
    attachment_id_from_ref,
    is_chat_attachment_ref,
)
from utils.markdown_outline import extract_outline, extract_preview, read_line_range


def test_chat_attachment_ref_parsing():
    ref = f"{CHAT_ATTACHMENT_REF}:abc-123"
    assert is_chat_attachment_ref(ref)
    assert attachment_id_from_ref(ref) == "abc-123"
    assert not is_chat_attachment_ref("inline text")


def test_extract_preview_truncates():
    text = "a" * 1000
    preview = extract_preview(text, max_chars=100)
    assert len(preview) == 101
    assert preview.endswith("…")


def test_extract_outline_headings():
    md = "# Title\n\n## Section\n\nbody"
    outline = extract_outline(md)
    assert "Title" in outline
    assert "Section" in outline


def test_read_line_range():
    md = "\n".join(f"line{i}" for i in range(10))
    chunk, offset, count, total = read_line_range(md, offset=2, limit=3)
    assert offset == 2
    assert count == 3
    assert total == 10
    assert "line2" in chunk


def test_detect_kind_document():
    from services.chat_attachment_service import _detect_kind

    assert _detect_kind("report.pdf", None) == "document"
    assert _detect_kind("photo.png", "image/png") == "image"


@pytest.mark.asyncio
async def test_upload_requires_existing_session():
    db = AsyncMock()
    with patch(
        "services.chat_attachment_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        from fastapi import HTTPException

        from services.chat_attachment_service import ChatAttachmentService

        with pytest.raises(HTTPException) as exc:
            await ChatAttachmentService._ensure_session_owned("sess-missing", "user-1", db)
        assert exc.value.status_code == 404
