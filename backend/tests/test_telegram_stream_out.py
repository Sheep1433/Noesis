"""Telegram 伪流式出站：节流 edit + 工具进度独立气泡。"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from domain.chat.delivery.events import WireFrame
from domain.chat.delivery.telegram.stream_out import (
    CURSOR,
    TelegramOutbound,
    TelegramTextStreamer,
    TelegramToolProgress,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeClient:
    def __init__(self) -> None:
        self.sends: List[Dict[str, Any]] = []
        self.edits: List[Dict[str, Any]] = []
        self._mid = 100

    async def send_message(self, chat_id, text, **kwargs):
        self._mid += 1
        self.sends.append({"chat_id": str(chat_id), "text": text, "message_id": self._mid})
        return {"message_id": self._mid}

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append(
            {"chat_id": str(chat_id), "message_id": int(message_id), "text": text}
        )
        return {"message_id": message_id}


@pytest.mark.asyncio
async def test_text_streamer_throttles_then_finalize_strips_cursor() -> None:
    clock = FakeClock()
    client = FakeClient()
    s = TelegramTextStreamer(
        client, "1", edit_interval=0.8, buffer_threshold=24, clock=clock
    )
    await s.on_delta("你好")
    assert len(client.sends) == 1
    assert client.sends[0]["text"].endswith(CURSOR)

    await s.on_delta("啊")  # 未达 interval/threshold，不 edit
    assert len(client.edits) == 0

    clock.advance(1.0)
    await s.on_delta("世界")
    assert len(client.edits) == 1
    assert CURSOR in client.edits[0]["text"]

    await s.force_close()
    assert len(client.edits) >= 2
    assert not client.edits[-1]["text"].endswith(CURSOR)
    assert "你好啊世界" in client.edits[-1]["text"]


@pytest.mark.asyncio
async def test_buffer_threshold_triggers_edit() -> None:
    clock = FakeClock()
    client = FakeClient()
    s = TelegramTextStreamer(
        client, "1", edit_interval=10.0, buffer_threshold=5, clock=clock
    )
    await s.on_delta("hi")
    assert len(client.sends) == 1
    await s.on_delta("12345")  # 自上次 flush 新增 >=5
    assert len(client.edits) == 1


@pytest.mark.asyncio
async def test_tool_progress_accumulate_separate_from_text() -> None:
    clock = FakeClock()
    client = FakeClient()
    out = TelegramOutbound(client, "9")
    out.text = TelegramTextStreamer(client, "9", clock=clock)
    out.tools = TelegramToolProgress(client, "9", edit_interval=0.0, clock=clock)

    await out.on_text_delta("先说一句")
    await out.on_tool_start("read_file", "/tmp/a", tool_call_id="t1")
    clock.advance(2.0)
    await out.on_tool_start("exec", "ls", tool_call_id="t2")
    await out.on_text_delta("结论")
    await out.finalize()

    # 文本气泡 + 进度气泡（至少两类 send）
    assert len(out.text.message_ids) >= 1
    assert len(out.tools.message_ids) >= 1
    # 文本与进度 message_id 不同
    assert out.text.message_ids[0] != out.tools.message_ids[0]
    progress_bodies = [e["text"] for e in client.edits if "⚙️" in e["text"]]
    progress_sends = [s["text"] for s in client.sends if "⚙️" in s["text"]]
    joined = "\n".join(progress_sends + progress_bodies)
    assert "read_file" in joined
    assert "exec" in joined


@pytest.mark.asyncio
async def test_feed_events_maps_wireframes() -> None:
    client = FakeClient()
    out = TelegramOutbound(client, "1")
    await out.feed_events(
        [
            WireFrame(event="text-delta", data={"delta": "Hi"}),
            WireFrame(
                event="tool-input-start",
                data={"name": "web_search", "tool_call_id": "c1"},
            ),
            WireFrame(
                event="tool-input-available",
                data={
                    "name": "web_search",
                    "tool_call_id": "c1",
                    "input": {"query": "noesis"},
                },
            ),
        ]
    )
    await out.finalize()
    assert any("Hi" in s["text"] for s in client.sends)
    assert any("web_search" in s["text"] for s in client.sends + [
        {"text": e["text"]} for e in client.edits
    ])
    # tool result 不应出现
    assert not any("tool-output" in (s.get("text") or "") for s in client.sends)
