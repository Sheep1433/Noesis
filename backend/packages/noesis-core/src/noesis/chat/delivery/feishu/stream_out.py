"""飞书文本伪流式：节流更新，失败时由调用方发送终态。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from noesis.chat.delivery.events import RunEvent, WireFrame
from .client import FeishuBotClient

MAX_TEXT = 3900


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    value = (text or "").strip() or "…"
    return [value[i:i + limit] for i in range(0, len(value), limit)]


class FeishuOutbound:
    def __init__(self, client: FeishuBotClient, chat_id: str, *, reply_message_id: str | None = None, edit_interval: float = 0.8, clock: Any = None) -> None:
        self.client = client
        self.chat_id = chat_id
        self.reply_message_id = reply_message_id
        self.edit_interval = edit_interval
        self.clock = clock or time.monotonic
        self.text = ""
        self.message_id: str | None = None
        self.last_edit_at = 0.0
        self._lock = asyncio.Lock()
        self.sent_any = False

    async def feed_events(self, events: list[RunEvent]) -> None:
        for event in events:
            if not isinstance(event, WireFrame) or event.event != "text-delta":
                continue
            delta = str(event.data.get("delta") or event.data.get("textDelta") or "")
            if not delta:
                continue
            async with self._lock:
                self.text += delta
                now = float(self.clock())
                if self.message_id is None or now - self.last_edit_at >= self.edit_interval:
                    await self._flush(cursor=True)

    async def _flush(self, *, cursor: bool) -> None:
        display = self.text[:MAX_TEXT].rstrip() + (" ▉" if cursor else "")
        try:
            if self.message_id:
                await self.client.update_text(self.message_id, display or "…")
            else:
                result = (
                    await self.client.reply_text(self.reply_message_id, display or "…")
                    if self.reply_message_id else await self.client.send_text(self.chat_id, display or "…")
                )
                self.message_id = str(result.get("message_id") or "") or None
                self.sent_any = True
            self.last_edit_at = float(self.clock())
        except Exception:
            return

    async def finalize(self) -> None:
        async with self._lock:
            if self.text and self.message_id:
                await self._flush(cursor=False)
            elif self.text:
                await self.deliver_final(self.text)

    async def deliver_final(self, text: str) -> None:
        for index, part in enumerate(split_text(text)):
            if index == 0 and self.reply_message_id:
                result = await self.client.reply_text(self.reply_message_id, part)
            else:
                result = await self.client.send_text(self.chat_id, part)
            self.message_id = str(result.get("message_id") or "") or self.message_id
            self.sent_any = True
