"""Attachment helpers for Agent (markdown outline, vision, VLM caption)."""

from noesis.runtime.attachments.image_prepare import (
    build_image_preview_base64,
    prepare_image_bytes_for_injection,
)
from noesis.runtime.attachments.markdown import extract_outline, extract_preview, read_line_range
from noesis.runtime.attachments.resolver import (
    CHAT_ATTACHMENT_REF,
    attachment_id_from_ref,
    is_chat_attachment_ref,
)
from noesis.runtime.attachments.vision import is_vision_available
from noesis.runtime.attachments.vlm_caption import describe_image_bytes_for_chat
from noesis.runtime.attachments.input_resolver import AttachmentInputResolver

__all__ = [
    "CHAT_ATTACHMENT_REF",
    "attachment_id_from_ref",
    "build_image_preview_base64",
    "describe_image_bytes_for_chat",
    "extract_outline",
    "extract_preview",
    "is_chat_attachment_ref",
    "is_vision_available",
    "prepare_image_bytes_for_injection",
    "read_line_range",
    "AttachmentInputResolver",
]
