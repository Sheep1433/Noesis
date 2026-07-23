"""Telegram ChannelAdapter：normalize Update + 出站投影（运行时主路径用 stream_out）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from domain.chat.delivery.channels import ChannelCapabilities, InboundMessage
from domain.chat.delivery.events import RunCompleted, RunEvent, WireFrame
from domain.chat.delivery.telegram.client import TelegramBotClient


def _extract_final_text(events: List[RunEvent]) -> str:
    chunks: List[str] = []
    for ev in events:
        if not isinstance(ev, WireFrame):
            continue
        if ev.event == "text-delta":
            delta = ev.data.get("delta") or ev.data.get("textDelta") or ""
            if delta:
                chunks.append(str(delta))
    if chunks:
        return "".join(chunks).strip()
    return ""


@dataclass
class TelegramChannelAdapter:
    """真收发 adapter；出站依赖注入的 client + target_chat_id。"""

    channel_type: str = "telegram"
    capabilities: ChannelCapabilities = field(
        default_factory=lambda: ChannelCapabilities(
            streaming_edit=True,
            max_text_len=4096,
            markdown=True,
            mirror_tools=False,
        )
    )
    client: Optional[TelegramBotClient] = None
    target_chat_id: Optional[str] = None
    started: bool = False
    last_sent_text: str = ""

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def normalize_inbound(self, raw: Dict[str, Any]) -> Optional[InboundMessage]:
        """接受完整 Update 或已抽出的 message dict。"""
        msg = raw.get("message") if isinstance(raw.get("message"), dict) else raw
        if not isinstance(msg, dict):
            return None
        text = msg.get("text") or msg.get("caption")
        if text is None:
            return None
        chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id = chat.get("id")
        if chat_id is None:
            chat_id = msg.get("chat_id")
        if chat_id is None:
            return None
        mid = msg.get("message_id")
        return InboundMessage(
            channel_type=self.channel_type,
            external_chat_id=str(chat_id),
            text=str(text),
            external_message_id=str(mid) if mid is not None else None,
            raw=raw if "message" in raw else {"message": msg},
        )

    async def project_outbound(self, events: List[RunEvent]) -> None:
        """兼容 SPI：无 streamer 时一次终态 send（运行时优先用 TelegramOutbound）。"""
        text = _extract_final_text(events)
        if not text:
            for ev in events:
                if isinstance(ev, RunCompleted):
                    text = "（已完成）"
                    break
                if isinstance(ev, WireFrame) and ev.event == "finish":
                    reason = ev.data.get("finish_reason") or "stop"
                    if reason == "hitl_pending":
                        text = "需要审批后继续。"
                    elif reason == "error":
                        text = "执行出错，请稍后重试或到网页查看。"
        if not text or not self.client or not self.target_chat_id:
            return
        body = text[: self.capabilities.max_text_len or 4096]
        await self.client.send_message(self.target_chat_id, body)
        self.last_sent_text = body


def extract_plain_text_from_parts(content: Dict[str, Any]) -> str:
    """从 assistant content.parts 提取纯文本。"""
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return ""
    chunks: List[str] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text" and p.get("content"):
            chunks.append(str(p["content"]))
    return "\n\n".join(chunks).strip()
