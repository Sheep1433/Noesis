"""Resolve chat attachments into the final HumanMessage before an Agent run."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from langchain_core.messages import HumanMessage, RemoveMessage
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import ChatAttachmentConfig
from noesis.runtime.attachments.image_prepare import prepare_image_bytes_for_injection
from noesis.runtime.attachments.markdown import extract_outline
from noesis.runtime.attachments.resolver import attachment_id_from_ref, is_chat_attachment_ref
from noesis.runtime.attachments.vision import is_vision_available
from noesis.runtime.attachments.vlm_caption import describe_image_bytes_for_chat
from noesis.runtime.context_provenance import estimate_source_tokens, get_or_create_context_provenance
from noesis.runtime.deps import require_attachment_service
from noesis.knowledge.embedding import is_vlm_configured
from noesis.runtime.logging import logger


def _human_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def _image_data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


class AttachmentInputResolver:
    """Build the one final HumanMessage for the current attachment round."""

    def __init__(
        self,
        *,
        session_id: str,
        user_id: str,
        db: AsyncSession,
        model_id: str | None = None,
        vision_available: bool | None = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.db = db
        self.model_id = model_id
        self.vision_available = (
            vision_available if vision_available is not None else is_vision_available(model_id)
        )
        self.vlm_fallback_enabled = (
            ChatAttachmentConfig.vlm_fallback_enabled
            and is_vlm_configured()
            and not self.vision_available
        )

    @staticmethod
    def _tag_attachments_provenance(
        uploaded_block: str,
        images: list[tuple[bytes, str, str]],
        image_delivery: str,
    ) -> None:
        """Tag attachment content as an ``attachments`` source for attribution.

        Estimates the injected attachment text (file listing + captions) plus a
        flat per-image cost for multimodal delivery. VLM captions are already in
        ``uploaded_block``; multimodal image tokens are approximated at the same
        flat rate the token counter uses (85/image). Best-effort: never blocks.
        """
        try:
            tokens = estimate_source_tokens(uploaded_block)
            if image_delivery == "multimodal" and images:
                tokens += 85 * len(images)
            if tokens > 0:
                get_or_create_context_provenance().add("attachments", tokens)
        except Exception:  # noqa: BLE001 - provenance is best-effort
            pass

    @staticmethod
    def _parse_meta(messages: list[Any], *, session_id: str, user_id: str) -> dict[str, Any] | None:
        for message in reversed(messages):
            if getattr(message, "type", None) != "human":
                continue
            meta = (getattr(message, "additional_kwargs", None) or {}).get("noesis_attachments")
            if isinstance(meta, dict):
                return meta
            return {"file_dict": {}, "session_id": session_id, "user_id": user_id}
        return None

    @staticmethod
    def _current_round_ids(file_dict: dict[str, Any]) -> set[str]:
        ids: set[str] = set()
        for value in file_dict.values():
            if value and is_chat_attachment_ref(str(value)):
                attachment_id = attachment_id_from_ref(str(value))
                if attachment_id:
                    ids.add(attachment_id)
        return ids

    async def _build_uploaded_files(
        self,
        file_dict: dict[str, Any],
        round_ids: set[str],
        *,
        image_delivery: str,
    ) -> tuple[str, bool]:
        service = require_attachment_service()
        docs = await service.list_session_documents(self.session_id, self.user_id, self.db)
        images = await service.list_session_images(self.session_id, self.user_id, self.db)
        if not docs and not images and not file_dict:
            return "", False

        lines = ["<uploaded_files>"]
        has_content = False
        for row in docs:
            text, _ = service._read_document_text(row)
            outline = extract_outline(text) if text else ""
            round_tag = "current" if row.id in round_ids else "history"
            lines.append(
                f'- document: "{row.file_name}" path="{row.virtual_path}" '
                f'chars={row.char_count} round="{round_tag}"'
            )
            if outline:
                lines.append(f"  outline:\n{outline}")
            if text and len(text) <= ChatAttachmentConfig.tiny_inline_chars:
                lines.append(f"  <inline>\n{text}\n  </inline>")
            has_content = True

        for row in images:
            round_tag = "current" if row.id in round_ids else "history"
            if self.vision_available and round_tag == "current":
                continue
            lines.append(
                f'- image: "{row.file_name}" mime="{row.mime_type or ""}" '
                f'round="{round_tag}"'
            )
            if image_delivery == "none":
                lines.append("  note: 当前模型不支持 Vision，且未配置 VLM 描述兜底，无法查看图片内容")
            elif image_delivery == "vlm_caption":
                lines.append("  note: 已通过 VLM 生成图片描述并注入正文（非原生看图）")
            has_content = True

        lines.append("</uploaded_files>")
        return "\n".join(lines), has_content

    async def _collect_images(
        self,
        file_dict: dict[str, Any],
        round_ids: set[str],
    ) -> list[tuple[bytes, str, str]]:
        service = require_attachment_service()
        selected: list[tuple[bytes, str, str]] = []
        max_files = ChatAttachmentConfig.max_files_per_message

        async def append_row(row: Any) -> bool:
            if len(selected) >= max_files:
                return False
            try:
                data, mime = service.read_image_bytes(row)
            except FileNotFoundError:
                logger.warning("图片文件缺失 attachment_id=%s", row.id)
                return True
            prepared, out_mime = prepare_image_bytes_for_injection(
                data,
                mime,
                max_edge=ChatAttachmentConfig.image_inject_max_edge,
            )
            selected.append((prepared, out_mime, row.file_name))
            return True

        for attachment_id in round_ids:
            row = await service.get_by_id(attachment_id, self.session_id, self.user_id, self.db)
            if row and row.kind == "image" and not await append_row(row):
                return selected

        if ChatAttachmentConfig.reinject_session_images and len(selected) < max_files:
            for row in await service.list_session_images(self.session_id, self.user_id, self.db):
                if row.id in round_ids:
                    continue
                if not await append_row(row):
                    break
        return selected

    async def _build_vlm_caption_block(self, images: list[tuple[bytes, str, str]]) -> str:
        lines: list[str] = []
        for data, mime, name in images:
            try:
                description = await asyncio.to_thread(
                    describe_image_bytes_for_chat,
                    data,
                    mime,
                    file_name=name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("VLM 描述失败 file=%r: %s", name, exc)
                description = "（描述生成失败，请仅依据文件名推断）"
            lines.append(f"[图片描述 · {name}]\n{description}")
        return "\n\n".join(lines).strip()

    async def _patch_last_human(self, messages: list[Any]) -> dict[str, list[Any]] | None:
        meta = self._parse_meta(messages, session_id=self.session_id, user_id=self.user_id)
        if meta is None:
            return None
        file_dict = meta.get("file_dict") or {}
        if not isinstance(file_dict, dict):
            file_dict = {}
        round_ids = self._current_round_ids(file_dict)

        image_delivery = "none"
        images: list[tuple[bytes, str, str]] = []
        if self.vision_available or self.vlm_fallback_enabled:
            images = await self._collect_images(file_dict, round_ids)
            if images:
                image_delivery = "multimodal" if self.vision_available else "vlm_caption"

        uploaded_block, has_files = await self._build_uploaded_files(
            file_dict,
            round_ids,
            image_delivery=image_delivery,
        )
        last_human = next(
            (message for message in reversed(messages) if getattr(message, "type", None) == "human"),
            None,
        )
        if last_human is None:
            return None

        user_text = _human_text(last_human.content)
        combined_text = f"{uploaded_block}\n\n{user_text}".strip() if has_files else user_text
        if image_delivery == "vlm_caption" and images:
            captions = await self._build_vlm_caption_block(images)
            if captions:
                combined_text = f"{combined_text}\n\n{captions}".strip()
        if not has_files and not images:
            return None

        if images and self.vision_available:
            content: Any = [{"type": "text", "text": combined_text}]
            content.extend(
                {"type": "image_url", "image_url": {"url": _image_data_uri(data, mime)}}
                for data, mime, _ in images
            )
        else:
            content = combined_text
        kwargs = dict(getattr(last_human, "additional_kwargs", None) or {})
        kwargs["noesis_attachments"] = meta
        new_human = HumanMessage(content=content, additional_kwargs=kwargs, id=getattr(last_human, "id", None))
        self._tag_attachments_provenance(uploaded_block, images, image_delivery)
        logger.info(
            "附件已解析 session=%s model_id=%s vision=%s vlm=%s images=%d delivery=%s",
            self.session_id,
            self.model_id or "",
            self.vision_available,
            self.vlm_fallback_enabled,
            len(images),
            image_delivery,
        )
        updates: list[Any] = []
        if getattr(last_human, "id", None):
            updates.append(RemoveMessage(id=last_human.id))
        updates.append(new_human)
        return {"messages": updates}

    async def resolve(self, messages: list[Any]) -> list[Any]:
        update = await self._patch_last_human(list(messages))
        if not update:
            return messages
        replacement = [item for item in update["messages"] if not isinstance(item, RemoveMessage)]
        if not replacement:
            return messages
        last_human = next(
            (index for index in range(len(messages) - 1, -1, -1) if getattr(messages[index], "type", None) == "human"),
            None,
        )
        if last_human is None:
            return messages
        return [*messages[:last_human], replacement[-1], *messages[last_human + 1 :]]

    async def resolve_human_message(
        self,
        query: str,
        *,
        additional_kwargs: dict[str, Any] | None = None,
    ) -> HumanMessage:
        resolved = await self.resolve([HumanMessage(content=query, additional_kwargs=additional_kwargs or {})])
        return resolved[-1]
