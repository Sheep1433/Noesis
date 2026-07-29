"""飞书事件规范化与 ChannelAdapter。"""
from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Any

from noesis_server.domain.chat.delivery.channels import ChannelCapabilities, InboundMessage
from noesis_server.domain.chat.delivery.events import RunEvent
from .client import FeishuBotClient


class EventDeduplicator:
    def __init__(self, ttl_seconds: float = 600, max_items: int = 4096) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._seen: OrderedDict[str, float] = OrderedDict()

    def accept(self, key: str, *, now: float | None = None) -> bool:
        if not key:
            return True
        current = time.monotonic() if now is None else now
        while self._seen:
            first, stamped = next(iter(self._seen.items()))
            if current - stamped <= self.ttl_seconds and len(self._seen) < self.max_items:
                break
            self._seen.pop(first, None)
        if key in self._seen:
            return False
        self._seen[key] = current
        return True


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return content.strip()
    return str(content.get("text") or "").strip() if isinstance(content, dict) else ""


def _dict_field(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


class FeishuChannelAdapter:
    channel_type = "feishu"
    capabilities = ChannelCapabilities(streaming_edit=True, max_text_len=4000, markdown=False, mirror_tools=False)

    def __init__(self, client: FeishuBotClient | None = None, *, deduplicator: EventDeduplicator | None = None) -> None:
        self.client = client
        self.deduplicator = deduplicator or EventDeduplicator()
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def normalize_inbound(self, raw: dict[str, Any]) -> InboundMessage | None:
        header = _dict_field(raw, "header")
        event = _dict_field(raw, "event") or raw
        message = _dict_field(event, "message")
        sender = _dict_field(event, "sender")
        sender_id = _dict_field(sender, "sender_id")
        open_id = str(sender_id.get("open_id") or "")
        message_id = str(message.get("message_id") or "")
        event_id = str(header.get("event_id") or raw.get("event_id") or "")
        if not self.deduplicator.accept(event_id or message_id):
            return None
        if str(message.get("message_type") or "text") != "text" or not open_id:
            return None
        text = _content_text(message.get("content"))
        chat_type = str(message.get("chat_type") or "")
        mentions = message.get("mentions") if isinstance(message.get("mentions"), list) else []
        if chat_type == "group":
            if not mentions:
                return None
            for mention in mentions:
                key = str(mention.get("key") or "") if isinstance(mention, dict) else ""
                if key:
                    text = text.replace(key, "").strip()
        if not text:
            return None
        return InboundMessage(
            channel_type="feishu",
            external_chat_id=open_id,
            text=text,
            external_message_id=message_id or None,
            # 授权绑定只使用 sender open_id；群 chat_id 仅保留在 raw 作为回复目标。
            thread_id=None,
            raw={**raw, "reply_chat_id": message.get("chat_id"), "event_id": event_id},
        )

    async def project_outbound(self, events: list[RunEvent]) -> None:
        if self.client is None:
            raise RuntimeError("feishu client is not configured")
