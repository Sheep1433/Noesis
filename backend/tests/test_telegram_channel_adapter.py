"""Telegram adapter normalize / final-only / runtime secrets。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from domain.chat.delivery.channels import ChannelBinding, channel_bindings
from domain.chat.delivery.events import RunCompleted, WireFrame
from domain.chat.delivery.channels import route_inbound
from domain.chat.delivery.telegram.adapter import (
    TelegramChannelAdapter,
    extract_plain_text_from_parts,
)
from domain.chat.delivery.telegram.client import mask_bot_token
from services.messaging_channel_service import MessagingChannelService


@pytest.mark.asyncio
async def test_normalize_telegram_update() -> None:
    ad = TelegramChannelAdapter()
    msg = await ad.normalize_inbound(
        {
            "update_id": 1,
            "message": {
                "message_id": 42,
                "text": "hello",
                "chat": {"id": 12345, "type": "private"},
            },
        }
    )
    assert msg is not None
    assert msg.external_chat_id == "12345"
    assert msg.text == "hello"
    assert msg.external_message_id == "42"


@pytest.mark.asyncio
async def test_normalize_skips_non_text() -> None:
    ad = TelegramChannelAdapter()
    assert (
        await ad.normalize_inbound(
            {"message": {"message_id": 1, "chat": {"id": 1}, "photo": []}}
        )
        is None
    )


@pytest.mark.asyncio
async def test_unpaired_rejects_agent_route() -> None:
    channel_bindings.clear()
    ad = TelegramChannelAdapter()
    inbound = await ad.normalize_inbound(
        {"message": {"message_id": 1, "text": "hi", "chat": {"id": 999}}}
    )
    assert inbound is not None
    result = route_inbound(inbound)
    assert result.ok is False
    assert result.reject_reason == "unpaired"


@pytest.mark.asyncio
async def test_paired_route_ok() -> None:
    channel_bindings.clear()
    channel_bindings.put(
        ChannelBinding(
            user_id="1",
            channel_type="telegram",
            external_chat_id="999",
            session_id="channel:abc",
        )
    )
    ad = TelegramChannelAdapter()
    inbound = await ad.normalize_inbound(
        {"message": {"message_id": 1, "text": "hi", "chat": {"id": 999}}}
    )
    assert inbound is not None
    result = route_inbound(inbound)
    assert result.ok is True
    assert result.binding is not None
    assert result.binding.session_id == "channel:abc"


@pytest.mark.asyncio
async def test_project_outbound_final_only_sends_once(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: List[Dict[str, Any]] = []

    class _FakeClient:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append({"chat_id": str(chat_id), "text": text})
            return {"message_id": 1}

    ad = TelegramChannelAdapter(client=_FakeClient(), target_chat_id="42")  # type: ignore[arg-type]
    events = [
        WireFrame(event="text-delta", data={"type": "text-delta", "delta": "你好"}),
        WireFrame(event="text-delta", data={"type": "text-delta", "delta": "世界"}),
        WireFrame(event="finish", data={"type": "finish", "finish_reason": "stop"}),
        RunCompleted(finish_reason="stop"),
    ]
    await ad.project_outbound(events)
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "42"
    assert sent[0]["text"] == "你好世界"


def test_mask_bot_token() -> None:
    assert mask_bot_token("123:ABCDEFGH") == "****EFGH"


def test_extract_plain_text_from_parts() -> None:
    assert (
        extract_plain_text_from_parts(
            {"parts": [{"type": "text", "content": "a"}, {"type": "tool", "name": "x"}]}
        )
        == "a"
    )


def test_iter_enabled_runtime_includes_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    users = tmp_path / "users"
    uid = "7"
    ch_dir = users / uid
    ch_dir.mkdir(parents=True)
    (ch_dir / "channels.json").write_text(
        """
{
  "channels": [
    {
      "channel_id": "c1",
      "type": "telegram",
      "enabled": true,
      "display_name": "bot",
      "secrets": {"bot_token": "123456:AAAsecretTOKEN"},
      "pairing": {"chat_id": "999"},
      "routing": {"default_qa_type": "SUPER_AGENT_QA", "default_session_id": "s-tg"}
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "services.messaging_channel_service._USERS_ROOT", users
    )
    monkeypatch.setattr(
        "services.messaging_channel_service.get_user_channels_path",
        lambda user_id: users / str(user_id) / "channels.json",
    )
    items = MessagingChannelService.iter_enabled_runtime("telegram")
    assert len(items) == 1
    assert items[0].bot_token.endswith("TOKEN")
    assert items[0].pairing_chat_id == "999"
    assert items[0].default_session_id == "s-tg"
    # HTTP 视图仍脱敏
    public = MessagingChannelService.list_channels(uid)
    assert "AAAsecret" not in str(public)
    assert public[0]["bot_token_masked"].startswith("****")
