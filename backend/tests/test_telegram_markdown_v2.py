"""Telegram MarkdownV2 最小转换 + 终态回落。"""
from __future__ import annotations

import pytest

from domain.chat.delivery.telegram.markdown_v2 import escape_mdv2, to_telegram_markdown_v2
from domain.chat.delivery.telegram.stream_out import (
    CURSOR,
    TelegramTextStreamer,
    deliver_final_markdown,
)


def test_escape_mdv2_specials() -> None:
    assert "\\." in escape_mdv2("v1.0")
    assert "\\_" in escape_mdv2("a_b")


def test_to_mdv2_bold_and_code() -> None:
    out = to_telegram_markdown_v2("说 **你好** 和 `x_y`")
    assert "*你好*" in out
    assert "`x_y`" in out
    # 普通句号被转义
    assert "说" in out


def test_to_mdv2_fenced_code_preserved() -> None:
    src = "见：\n```python\na = 1.0\n```\n完"
    out = to_telegram_markdown_v2(src)
    assert "```python" in out
    assert "a = 1.0" in out


def test_to_mdv2_link() -> None:
    out = to_telegram_markdown_v2("[Noesis](https://example.com/a.b)")
    assert out.startswith("[Noesis](")
    assert "example.com" in out


@pytest.mark.asyncio
async def test_force_close_tries_markdown_then_plain() -> None:
    calls: list[dict] = []

    class Client:
        async def send_message(self, chat_id, text, **kwargs):
            calls.append({"op": "send", "text": text, **kwargs})
            if kwargs.get("parse_mode") == "MarkdownV2":
                raise RuntimeError("can't parse entities")
            return {"message_id": 7}

        async def edit_message_text(self, chat_id, message_id, text, **kwargs):
            calls.append(
                {"op": "edit", "message_id": message_id, "text": text, **kwargs}
            )
            if kwargs.get("parse_mode") == "MarkdownV2":
                raise RuntimeError("can't parse entities")
            return {"message_id": message_id}

    client = Client()
    s = TelegramTextStreamer(client, "1", edit_interval=0.01, buffer_threshold=1)
    await s.on_delta("**hi**")
    assert any(CURSOR in c["text"] for c in calls if c["op"] == "send")
    await s.force_close()
    # 终态先 MarkdownV2 edit，失败再 plain edit
    md_tries = [c for c in calls if c.get("parse_mode") == "MarkdownV2"]
    plain_final = [
        c
        for c in calls
        if c.get("op") == "edit" and c.get("parse_mode") is None and "hi" in c["text"]
    ]
    assert md_tries
    assert plain_final
    assert CURSOR not in plain_final[-1]["text"]


@pytest.mark.asyncio
async def test_deliver_final_markdown_send_fallback() -> None:
    class Client:
        async def send_message(self, chat_id, text, **kwargs):
            if kwargs.get("parse_mode") == "MarkdownV2":
                raise RuntimeError("bad md")
            return {"message_id": 3}

        async def edit_message_text(self, *a, **k):
            raise AssertionError("should not edit")

    mid = await deliver_final_markdown(Client(), "1", "hello **x**")
    assert mid == 3
