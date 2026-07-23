"""Telegram 出站伪流式：对齐 Hermes 双管道（文本 edit + 工具进度独立气泡）。"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Protocol

from domain.chat.delivery.events import RunEvent, WireFrame
from domain.chat.delivery.telegram.markdown_v2 import to_telegram_markdown_v2

# Hermes gateway/config.py defaults
EDIT_INTERVAL = 0.8
BUFFER_THRESHOLD = 24
CURSOR = " ▉"
TOOL_EDIT_INTERVAL = 1.5
TOOL_PREVIEW_LEN = 40
MAX_TEXT = 4096
# 转义后可能变长；终态转换前先裁到稍短
_MD_PLAIN_BUDGET = 3500


async def deliver_final_markdown(
    client: _TgClient,
    chat_id: str | int,
    text: str,
    *,
    message_id: Optional[int] = None,
) -> Optional[int]:
    """终态：先试 MarkdownV2，失败回落 plain。返回 message_id。"""
    plain = _clip((text or "").strip() or "…", _MD_PLAIN_BUDGET)
    md = _clip(to_telegram_markdown_v2(plain))
    try:
        if message_id is not None:
            await client.edit_message_text(
                chat_id, message_id, md, parse_mode="MarkdownV2"
            )
            return message_id
        result = await client.send_message(chat_id, md, parse_mode="MarkdownV2")
        mid = result.get("message_id") if isinstance(result, dict) else None
        return int(mid) if mid is not None else None
    except Exception:
        if message_id is not None:
            await client.edit_message_text(chat_id, message_id, plain)
            return message_id
        result = await client.send_message(chat_id, plain)
        mid = result.get("message_id") if isinstance(result, dict) else None
        return int(mid) if mid is not None else None


class _TgClient(Protocol):
    async def send_message(
        self, chat_id: str | int, text: str, **kwargs: Any
    ) -> Dict[str, Any]: ...

    async def edit_message_text(
        self, chat_id: str | int, message_id: int, text: str, **kwargs: Any
    ) -> Dict[str, Any]: ...


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    t = text or ""
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 1)] + "…"


def _preview_from_input(raw: Any, limit: int = TOOL_PREVIEW_LEN) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        s = raw.strip()
    elif isinstance(raw, dict):
        for key in ("query", "command", "path", "file_path", "url", "prompt", "content"):
            if raw.get(key):
                s = str(raw[key]).strip()
                break
        else:
            try:
                s = json.dumps(raw, ensure_ascii=False)
            except (TypeError, ValueError):
                s = str(raw)
    else:
        s = str(raw).strip()
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


def _tool_line(name: str, preview: str = "") -> str:
    label = (name or "tool").strip() or "tool"
    if preview:
        return f"⚙️ {label}: {preview}"
    return f"⚙️ {label}"


class TelegramTextStreamer:
    """placeholder send + 节流 editMessageText；finalize 去掉 cursor。"""

    def __init__(
        self,
        client: _TgClient,
        chat_id: str | int,
        *,
        edit_interval: float = EDIT_INTERVAL,
        buffer_threshold: int = BUFFER_THRESHOLD,
        cursor: str = CURSOR,
        clock: Optional[Any] = None,
    ) -> None:
        self._client = client
        self._chat_id = chat_id
        self._edit_interval = edit_interval
        self._buffer_threshold = buffer_threshold
        self._cursor = cursor
        self._clock = clock or time.monotonic
        self._text = ""
        self._message_id: Optional[int] = None
        self._last_sent = ""
        self._last_edit_at = 0.0
        self._chars_since_edit = 0
        self._closed = False
        self._lock = asyncio.Lock()
        self.message_ids: List[int] = []

    @property
    def message_id(self) -> Optional[int]:
        return self._message_id

    @property
    def has_content(self) -> bool:
        return bool(self._text.strip()) or self._message_id is not None

    async def on_delta(self, delta: str) -> None:
        if not delta or self._closed:
            return
        async with self._lock:
            self._text += delta
            self._chars_since_edit += len(delta)
            now = float(self._clock())
            due = (
                self._message_id is None
                or (now - self._last_edit_at) >= self._edit_interval
                or self._chars_since_edit >= self._buffer_threshold
            )
            if due:
                await self._flush_locked(with_cursor=True)

    async def finalize_segment(self) -> bool:
        """收口当前文本气泡（去 cursor）；返回是否发过内容。准备下一段。"""
        async with self._lock:
            had = bool(self._text.strip()) or self._message_id is not None
            if had:
                await self._flush_locked(with_cursor=False)
            self._message_id = None
            self._text = ""
            self._last_sent = ""
            self._chars_since_edit = 0
            return had

    async def force_close(self) -> None:
        async with self._lock:
            body = self._text.strip()
            if not body and self._message_id is None:
                self._closed = True
                return
            plain = body if body else "…"
            mid = await deliver_final_markdown(
                self._client,
                self._chat_id,
                plain,
                message_id=self._message_id,
            )
            if mid is not None and self._message_id is None:
                self._message_id = mid
                self.message_ids.append(mid)
            self._last_sent = plain
            self._closed = True

    async def _flush_locked(self, *, with_cursor: bool) -> None:
        # 流式过程保留原文空白；仅终态去掉首尾空白，避免把「尚未结束的尾空格」当成完成态
        raw = self._text
        if with_cursor:
            body = raw.rstrip("\n")
        else:
            body = raw.strip()
        if not body and self._message_id is None:
            return
        display = body if body else "…"
        if with_cursor:
            display = _clip(display + self._cursor)
        else:
            display = _clip(display)
        if display == self._last_sent and self._message_id is not None:
            self._chars_since_edit = 0
            self._last_edit_at = float(self._clock())
            return
        try:
            if self._message_id is None:
                result = await self._client.send_message(self._chat_id, display)
                mid = result.get("message_id") if isinstance(result, dict) else None
                if mid is not None:
                    self._message_id = int(mid)
                    self.message_ids.append(self._message_id)
            else:
                await self._client.edit_message_text(
                    self._chat_id, self._message_id, display
                )
        except Exception:
            # flood / message not modified：跳过本帧，不打断 Agent
            return
        self._last_sent = display
        self._chars_since_edit = 0
        self._last_edit_at = float(self._clock())


class TelegramToolProgress:
    """独立进度气泡；accumulate + 1.5s 节流；不镜像 tool result。"""

    def __init__(
        self,
        client: _TgClient,
        chat_id: str | int,
        *,
        edit_interval: float = TOOL_EDIT_INTERVAL,
        clock: Optional[Any] = None,
    ) -> None:
        self._client = client
        self._chat_id = chat_id
        self._edit_interval = edit_interval
        self._clock = clock or time.monotonic
        self._lines: List[str] = []
        self._message_id: Optional[int] = None
        self._last_sent = ""
        self._last_edit_at = 0.0
        self._dirty = False
        self._lock = asyncio.Lock()
        self.message_ids: List[int] = []

    async def append_line(self, line: str, *, force: bool = False) -> int:
        async with self._lock:
            self._lines.append(line)
            idx = len(self._lines) - 1
            self._dirty = True
            now = float(self._clock())
            if (
                force
                or self._message_id is None
                or (now - self._last_edit_at) >= self._edit_interval
            ):
                await self._flush_locked()
            return idx

    async def update_line(self, index: int, line: str) -> None:
        async with self._lock:
            if 0 <= index < len(self._lines):
                self._lines[index] = line
                self._dirty = True
                await self._flush_locked()

    async def flush(self) -> None:
        async with self._lock:
            if self._dirty:
                await self._flush_locked()

    async def reset_bubble(self) -> None:
        async with self._lock:
            if self._dirty:
                await self._flush_locked()
            self._message_id = None
            self._lines = []
            self._last_sent = ""
            self._dirty = False

    async def _flush_locked(self) -> None:
        if not self._lines:
            self._dirty = False
            return
        body = _clip("\n".join(self._lines))
        if body == self._last_sent and self._message_id is not None:
            self._dirty = False
            self._last_edit_at = float(self._clock())
            return
        try:
            if self._message_id is None:
                result = await self._client.send_message(self._chat_id, body)
                mid = result.get("message_id") if isinstance(result, dict) else None
                if mid is not None:
                    self._message_id = int(mid)
                    self.message_ids.append(self._message_id)
            else:
                await self._client.edit_message_text(
                    self._chat_id, self._message_id, body
                )
        except Exception:
            return
        self._last_sent = body
        self._dirty = False
        self._last_edit_at = float(self._clock())


class TelegramOutbound:
    """编排文本流 + 工具进度；供 channel_run on_events 调用。"""

    def __init__(self, client: _TgClient, chat_id: str | int) -> None:
        self.text = TelegramTextStreamer(client, chat_id)
        self.tools = TelegramToolProgress(client, chat_id)
        self._seen_tool_calls: set[str] = set()
        self._tool_line_index: Dict[str, int] = {}

    async def on_text_delta(self, delta: str) -> None:
        await self.text.on_delta(delta)

    async def on_tool_start(self, name: str, preview: str = "", *, tool_call_id: str = "") -> None:
        key = tool_call_id or f"{name}:{preview}"
        if key in self._seen_tool_calls:
            if preview and key in self._tool_line_index:
                await self.tools.update_line(
                    self._tool_line_index[key], _tool_line(name, preview)
                )
            return

        # 若刚有文本段，收口文本并开新进度气泡（Hermes content → __reset__）
        if self.text.has_content:
            await self.text.finalize_segment()
            await self.tools.reset_bubble()
            self._tool_line_index.clear()
        elif not self._seen_tool_calls:
            # 尚无文本：仅确保文本侧干净
            await self.text.finalize_segment()

        self._seen_tool_calls.add(key)
        idx = await self.tools.append_line(_tool_line(name, preview))
        self._tool_line_index[key] = idx

    async def finalize(self) -> None:
        await self.tools.flush()
        await self.text.force_close()

    @property
    def sent_any(self) -> bool:
        return bool(self.text.message_ids or self.tools.message_ids)

    async def feed_events(self, events: List[RunEvent]) -> None:
        for ev in events:
            if not isinstance(ev, WireFrame):
                continue
            if ev.event == "text-delta":
                delta = ev.data.get("delta") or ev.data.get("textDelta") or ""
                if delta:
                    await self.on_text_delta(str(delta))
            elif ev.event in ("tool-input-start", "tool-input-available"):
                name = str(ev.data.get("name") or ev.data.get("toolName") or "tool")
                preview = (
                    _preview_from_input(ev.data.get("input"))
                    if ev.data.get("input") is not None
                    else ""
                )
                tid = str(ev.data.get("tool_call_id") or "") or f"anon:{name}"
                await self.on_tool_start(name, preview, tool_call_id=tid)
