"""ChatAttachmentsMiddleware 单测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from agent.middlewares.chat_attachments_middleware import ChatAttachmentsMiddleware
from models.chat_models import TChatAttachment
from domain.chat.attachments.resolver import CHAT_ATTACHMENT_REF


def _doc_row(**overrides) -> TChatAttachment:
    base = dict(
        id="doc-1",
        session_id="sess-1",
        user_id="user-1",
        file_name="notes.md",
        kind="document",
        original_path="sessions/sess-1/uploads/notes.md",
        markdown_path="sessions/sess-1/attachments/notes.md",
        mime_type="text/markdown",
        virtual_path="/sessions/sess-1/attachments/notes.md",
        char_count=20,
        status="parsed",
        preview_base64=None,
        created_at=1,
        expires_at=9_999_999_999_999,
    )
    base.update(overrides)
    return TChatAttachment(**base)


def _img_row(**overrides) -> TChatAttachment:
    defaults = dict(
        id="img-1",
        file_name="photo.png",
        kind="image",
        mime_type="image/png",
        virtual_path="/sessions/sess-1/uploads/photo.png",
        markdown_path=None,
    )
    defaults.update(overrides)
    return _doc_row(**defaults)


@pytest.mark.asyncio
async def test_middleware_injects_uploaded_files_for_document():
    db = AsyncMock()
    row = _doc_row()
    ref = f"{CHAT_ATTACHMENT_REF}:{row.id}"

    with (
        patch(
            "agent.middlewares.chat_attachments_middleware.is_vlm_configured",
            return_value=False,
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_documents",
            new_callable=AsyncMock,
            return_value=[row],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_images",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService._read_document_text",
            return_value=("# Title\n\nhello world", None),
        ),
    ):
        mw = ChatAttachmentsMiddleware(session_id="sess-1", user_id="user-1", db=db, vision_available=False)
        msg = HumanMessage(
            content="总结文档",
            additional_kwargs={
                "noesis_attachments": {
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "file_dict": {"notes.md": ref},
                }
            },
        )
        result = await mw.abefore_agent({"messages": [msg]}, MagicMock())

    assert result is not None
    new_msg = result["messages"][-1]
    text = new_msg.content
    assert "<uploaded_files>" in text
    assert "notes.md" in text
    assert "总结文档" in text


@pytest.mark.asyncio
async def test_middleware_multimodal_when_vision_available():
    db = AsyncMock()
    mw = ChatAttachmentsMiddleware(session_id="sess-1", user_id="user-1", db=db, vision_available=True)
    row = _img_row()
    ref = f"{CHAT_ATTACHMENT_REF}:{row.id}"

    with (
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_documents",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_images",
            new_callable=AsyncMock,
            return_value=[row],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.get_by_id",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.read_image_bytes",
            return_value=(b"\x89PNG", "image/png"),
        ),
    ):
        msg = HumanMessage(
            content="描述图片",
            additional_kwargs={
                "noesis_attachments": {
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "file_dict": {"photo.png": ref},
                }
            },
        )
        result = await mw.abefore_agent({"messages": [msg]}, MagicMock())

    assert result is not None
    blocks = result["messages"][-1].content
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "text"
    assert any(b.get("type") == "image_url" for b in blocks)


@pytest.mark.asyncio
async def test_middleware_vision_downgrade_lists_image_metadata():
    db = AsyncMock()
    row = _img_row()

    with (
        patch(
            "agent.middlewares.chat_attachments_middleware.is_vlm_configured",
            return_value=False,
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_documents",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_images",
            new_callable=AsyncMock,
            return_value=[row],
        ),
    ):
        mw = ChatAttachmentsMiddleware(session_id="sess-1", user_id="user-1", db=db, vision_available=False)
        msg = HumanMessage(
            content="这是什么图",
            additional_kwargs={
                "noesis_attachments": {
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "file_dict": {row.file_name: f"{CHAT_ATTACHMENT_REF}:{row.id}"},
                }
            },
        )
        result = await mw.abefore_agent({"messages": [msg]}, MagicMock())

    text = result["messages"][-1].content
    assert isinstance(text, str)
    assert "无法查看图片" in text


@pytest.mark.asyncio
async def test_middleware_vlm_fallback_injects_caption():
    db = AsyncMock()
    row = _img_row()
    ref = f"{CHAT_ATTACHMENT_REF}:{row.id}"
    mock_cfg = MagicMock()
    mock_cfg.vlm_fallback_enabled = True
    mock_cfg.max_files_per_message = 10
    mock_cfg.reinject_session_images = True
    mock_cfg.image_inject_max_edge = 1536
    mock_cfg.tiny_inline_chars = 4096

    with (
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentConfig",
            mock_cfg,
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.is_vlm_configured",
            return_value=True,
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_documents",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_images",
            new_callable=AsyncMock,
            return_value=[row],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.get_by_id",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.read_image_bytes",
            return_value=(b"\x89PNG", "image/png"),
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.describe_image_bytes_for_chat",
            return_value="图中是一只猫",
        ),
    ):
        mw = ChatAttachmentsMiddleware(
            session_id="sess-1", user_id="user-1", db=db, vision_available=False
        )
        msg = HumanMessage(
            content="描述图片",
            additional_kwargs={
                "noesis_attachments": {
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "file_dict": {"photo.png": ref},
                }
            },
        )
        result = await mw.abefore_agent({"messages": [msg]}, MagicMock())

    text = result["messages"][-1].content
    assert isinstance(text, str)
    assert "[图片描述 · photo.png]" in text
    assert "图中是一只猫" in text
    assert "VLM 生成图片描述" in text


@pytest.mark.asyncio
async def test_middleware_reinjects_historical_images():
    db = AsyncMock()
    mw = ChatAttachmentsMiddleware(session_id="sess-1", user_id="user-1", db=db, vision_available=True)
    historical = _img_row(id="img-old", file_name="old.png")

    with (
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_documents",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_images",
            new_callable=AsyncMock,
            return_value=[historical],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.read_image_bytes",
            return_value=(b"img", "image/png"),
        ),
    ):
        msg = HumanMessage(
            content="继续问",
            additional_kwargs={
                "noesis_attachments": {
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "file_dict": {},
                }
            },
        )
        result = await mw.abefore_agent({"messages": [msg]}, MagicMock())

    blocks = result["messages"][-1].content
    assert any(b.get("type") == "image_url" for b in blocks)


@pytest.mark.asyncio
async def test_middleware_noop_without_attachments():
    db = AsyncMock()
    mw = ChatAttachmentsMiddleware(session_id="sess-1", user_id="user-1", db=db)

    with (
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_documents",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "agent.middlewares.chat_attachments_middleware.ChatAttachmentService.list_session_images",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        msg = HumanMessage(content="纯文本")
        result = await mw.abefore_agent({"messages": [msg]}, MagicMock())

    assert result is None
