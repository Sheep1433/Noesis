"""会话附件中间件：仅 before_agent（文档清单 + multimodal 图片）。"""
from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import override

from config.env import ChatAttachmentConfig
from models.chat_models import TChatAttachment
from services.chat_attachment_service import ChatAttachmentService
from domain.chat.attachments.resolver import attachment_id_from_ref, is_chat_attachment_ref
from common.logging import logger
from domain.chat.attachments.markdown import extract_outline
from domain.chat.attachments.image_prepare import prepare_image_bytes_for_injection
from domain.chat.attachments.vision import is_vision_available
from domain.chat.attachments.vlm_caption import describe_image_bytes_for_chat
from kb.embedding import is_vlm_configured


def _human_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")


def _image_data_uri(data: bytes, mime: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class ChatAttachmentsMiddleware(AgentMiddleware[AgentState]):
    """在 Agent 执行前注入 uploaded_files 与 multimodal 图片。"""

    def __init__(
        self,
        *,
        session_id: str,
        user_id: str,
        db: AsyncSession,
        model_id: Optional[str] = None,
        vision_available: Optional[bool] = None,
    ):
        super().__init__()
        self.session_id = session_id
        self.user_id = user_id
        self.db = db
        self.model_id = model_id
        self.vision_available = (
            vision_available
            if vision_available is not None
            else is_vision_available(model_id)
        )
        self.vlm_fallback_enabled = (
            ChatAttachmentConfig.vlm_fallback_enabled
            and is_vlm_configured()
            and not self.vision_available
        )

    def _parse_meta(self, messages: list) -> Optional[Dict[str, Any]]:
        for msg in reversed(messages):
            if getattr(msg, "type", None) != "human":
                continue
            kwargs = getattr(msg, "additional_kwargs", None) or {}
            meta = kwargs.get("noesis_attachments")
            if isinstance(meta, dict):
                return meta
            return {"file_dict": {}, "session_id": self.session_id, "user_id": self.user_id}
        return None

    async def _current_round_ids(self, file_dict: Dict[str, Any]) -> Set[str]:
        ids: Set[str] = set()
        for val in (file_dict or {}).values():
            if val and is_chat_attachment_ref(str(val)):
                aid = attachment_id_from_ref(str(val))
                if aid:
                    ids.add(aid)
        return ids

    async def _build_uploaded_files(
        self,
        file_dict: Dict[str, Any],
        round_ids: Set[str],
        *,
        image_delivery: str,
    ) -> Tuple[str, bool]:
        docs = await ChatAttachmentService.list_session_documents(
            self.session_id, self.user_id, self.db
        )
        images = await ChatAttachmentService.list_session_images(
            self.session_id, self.user_id, self.db
        )
        if not docs and not images and not file_dict:
            return "", False

        lines: List[str] = ["<uploaded_files>"]
        has_content = False

        for row in docs:
            text, _ = ChatAttachmentService._read_document_text(row)
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
                lines.append(
                    "  note: 当前模型不支持 Vision，且未配置 VLM 描述兜底，无法查看图片内容"
                )
            elif image_delivery == "vlm_caption":
                lines.append("  note: 已通过 VLM 生成图片描述并注入正文（非原生看图）")
            has_content = True

        lines.append("</uploaded_files>")
        return "\n".join(lines), has_content

    async def _collect_images(
        self,
        file_dict: Dict[str, Any],
        round_ids: Set[str],
    ) -> List[Tuple[bytes, str, str]]:
        """返回 (bytes, mime, file_name) 列表；本轮优先，历史重注入总数受 max_files_per_message 限制。"""
        max_n = ChatAttachmentConfig.max_files_per_message
        selected: List[Tuple[bytes, str, str]] = []

        async def append_row(row: TChatAttachment) -> bool:
            if len(selected) >= max_n:
                return False
            try:
                data, mime = ChatAttachmentService.read_image_bytes(row)
            except FileNotFoundError:
                logger.warning(f"图片文件缺失 attachment_id={row.id}")
                return True
            prepared, out_mime = prepare_image_bytes_for_injection(
                data,
                mime,
                max_edge=ChatAttachmentConfig.image_inject_max_edge,
            )
            selected.append((prepared, out_mime, row.file_name))
            return True

        if round_ids:
            for aid in round_ids:
                row = await ChatAttachmentService.get_by_id(
                    aid, self.session_id, self.user_id, self.db
                )
                if row and row.kind == "image":
                    if not await append_row(row):
                        return selected

        if (
            ChatAttachmentConfig.reinject_session_images
            and len(selected) < max_n
        ):
            for row in await ChatAttachmentService.list_session_images(
                self.session_id, self.user_id, self.db
            ):
                if row.id in round_ids:
                    continue
                if not await append_row(row):
                    break

        return selected

    async def _build_vlm_caption_block(
        self,
        images: List[Tuple[bytes, str, str]],
    ) -> str:
        lines: List[str] = []
        for data, mime, name in images:
            try:
                desc = await asyncio.to_thread(
                    describe_image_bytes_for_chat,
                    data,
                    mime,
                    file_name=name,
                )
                lines.append(f"[图片描述 · {name}]\n{desc}")
            except Exception as exc:
                logger.warning(
                    f"[ChatAttachmentsMiddleware] VLM 描述失败 file={name!r}: {exc}"
                )
                lines.append(f"[图片描述 · {name}]\n（描述生成失败，请仅依据文件名推断）")
        return "\n\n".join(lines).strip()

    async def _patch_last_human(self, messages: list) -> dict | None:
        meta = self._parse_meta(messages)
        if meta is None:
            return None

        file_dict = meta.get("file_dict") or {}
        if not isinstance(file_dict, dict):
            file_dict = {}

        round_ids = await self._current_round_ids(file_dict)

        image_delivery = "none"
        images: List[Tuple[bytes, str, str]] = []
        if self.vision_available:
            images = await self._collect_images(file_dict, round_ids)
            if images:
                image_delivery = "multimodal"
        elif self.vlm_fallback_enabled:
            images = await self._collect_images(file_dict, round_ids)
            if images:
                image_delivery = "vlm_caption"

        uploaded_block, has_files = await self._build_uploaded_files(
            file_dict, round_ids, image_delivery=image_delivery
        )

        last_human_idx = None
        last_human = None
        for i in range(len(messages) - 1, -1, -1):
            if getattr(messages[i], "type", None) == "human":
                last_human_idx = i
                last_human = messages[i]
                break

        if last_human is None:
            return None

        user_text = _human_text(last_human.content)
        prefix = f"{uploaded_block}\n\n" if has_files and uploaded_block else ""
        combined_text = f"{prefix}{user_text}".strip() if prefix else user_text

        vlm_caption_block = ""
        if image_delivery == "vlm_caption" and images:
            vlm_caption_block = await self._build_vlm_caption_block(images)
            if vlm_caption_block:
                combined_text = f"{combined_text}\n\n{vlm_caption_block}".strip()

        if not has_files and not images:
            return None

        if images and self.vision_available:
            content_blocks: List[dict] = [
                {"type": "text", "text": combined_text},
            ]
            for data, mime, _name in images:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_uri(data, mime)},
                    }
                )
            new_content: Any = content_blocks
        else:
            new_content = combined_text

        new_kwargs = dict(getattr(last_human, "additional_kwargs", None) or {})
        new_kwargs["noesis_attachments"] = meta

        new_human = HumanMessage(
            content=new_content,
            additional_kwargs=new_kwargs,
            id=getattr(last_human, "id", None),
        )

        logger.info(
            f"[ChatAttachmentsMiddleware] session={self.session_id} "
            f"model_id={self.model_id or ''} vision_available={self.vision_available} "
            f"vlm_fallback={self.vlm_fallback_enabled} images_injected={len(images)} "
            f"delivery={image_delivery}"
        )

        updates: list = []
        if getattr(last_human, "id", None):
            updates.append(RemoveMessage(id=last_human.id))
        updates.append(new_human)
        return {"messages": updates}

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        if not ChatAttachmentConfig.enabled:
            return None
        return await self._patch_last_human(messages)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        return None
