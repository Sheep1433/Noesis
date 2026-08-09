"""Attachment input resolver regressions after removing the middleware adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from noesis.runtime.attachments.input_resolver import AttachmentInputResolver


def _resolver(*, vision: bool = False) -> AttachmentInputResolver:
    with patch(
        "noesis.runtime.attachments.input_resolver.is_vlm_configured",
        return_value=False,
    ):
        return AttachmentInputResolver(
            session_id="session-1",
            user_id="user-1",
            db=AsyncMock(),
            vision_available=vision,
        )


@pytest.mark.asyncio
async def test_plain_message_is_unchanged_without_attachments() -> None:
    message = HumanMessage(content="hello")
    service = MagicMock()
    service.list_session_documents = AsyncMock(return_value=[])
    service.list_session_images = AsyncMock(return_value=[])
    with patch(
        "noesis.runtime.attachments.input_resolver.require_attachment_service",
        return_value=service,
    ):
        assert await _resolver().resolve([message]) == [message]


@pytest.mark.asyncio
async def test_resume_style_input_does_not_inject_without_human_message() -> None:
    assert await _resolver().resolve([]) == []


@pytest.mark.asyncio
async def test_resolver_keeps_attachment_metadata_on_final_human_message() -> None:
    metadata = {
        "session_id": "session-1",
        "user_id": "user-1",
        "file_dict": {"notes.md": "x" * 900},
    }
    resolver = _resolver()
    with patch.object(
        resolver,
        "_build_uploaded_files",
        AsyncMock(return_value=("### notes.md\nbody", True)),
    ):
        resolved = await resolver.resolve_human_message(
            "question",
            additional_kwargs={"noesis_attachments": metadata},
        )

    assert "notes.md" in str(resolved.content)
    assert resolved.additional_kwargs["noesis_attachments"] == metadata


@pytest.mark.asyncio
async def test_vision_image_becomes_multimodal_block() -> None:
    resolver = _resolver(vision=True)
    resolver._collect_images = AsyncMock(return_value=[(b"image", "image/png", "shot.png")])
    resolver._build_uploaded_files = AsyncMock(return_value=("", False))

    resolved = await resolver.resolve_human_message(
        "inspect",
        additional_kwargs={"noesis_attachments": {"file_dict": {}}},
    )

    assert isinstance(resolved.content, list)
    assert resolved.content[-1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_non_vision_model_uses_vlm_caption_without_image_block() -> None:
    with patch(
        "noesis.runtime.attachments.input_resolver.is_vlm_configured",
        return_value=True,
    ):
        resolver = AttachmentInputResolver(
            session_id="session-1",
            user_id="user-1",
            db=AsyncMock(),
            vision_available=False,
        )
    resolver._collect_images = AsyncMock(return_value=[(b"image", "image/png", "shot.png")])
    resolver._build_uploaded_files = AsyncMock(return_value=("", False))
    resolver._build_vlm_caption_block = AsyncMock(return_value="[图片描述] dashboard")

    resolved = await resolver.resolve_human_message(
        "inspect",
        additional_kwargs={"noesis_attachments": {"file_dict": {}}},
    )

    assert isinstance(resolved.content, str)
    assert "dashboard" in resolved.content
