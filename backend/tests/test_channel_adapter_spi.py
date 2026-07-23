"""ChannelAdapter SPI / Binding / 出站投影。"""
from __future__ import annotations

import pytest

from domain.chat.delivery.channels import (
    ChannelBinding,
    ChannelBindingStore,
    build_default_registry,
    project_for_capabilities,
)
from domain.chat.delivery.events import RunCompleted, WireFrame


def test_registry_resolves_telegram_and_wechat() -> None:
    reg = build_default_registry()
    assert reg.get("telegram") is not None
    assert reg.get("wechat") is not None
    assert reg.require("telegram").capabilities.streaming_edit is True
    assert reg.require("wechat").capabilities.streaming_edit is False


def test_unpaired_binding_rejects() -> None:
    store = ChannelBindingStore()
    assert store.resolve("telegram", "chat-1") is None


def test_binding_resolve_after_put() -> None:
    store = ChannelBindingStore()
    store.put(
        ChannelBinding(
            user_id="u1",
            channel_type="telegram",
            external_chat_id="chat-1",
            session_id="s1",
        )
    )
    b = store.resolve("telegram", "chat-1")
    assert b is not None
    assert b.session_id == "s1"


@pytest.mark.asyncio
async def test_outbound_projection_final_only_vs_streaming() -> None:
    reg = build_default_registry()
    events = [
        WireFrame(event="text-delta", data={"type": "text-delta", "delta": "a"}),
        WireFrame(event="finish", data={"type": "finish", "finish_reason": "stop"}),
        RunCompleted(finish_reason="stop"),
    ]
    tg = project_for_capabilities(events, reg.require("telegram").capabilities)
    wx = project_for_capabilities(events, reg.require("wechat").capabilities)
    assert len(tg) == 3
    assert len(wx) == 2
    assert all(
        isinstance(e, (RunCompleted, WireFrame))
        and (not isinstance(e, WireFrame) or e.event == "finish")
        for e in wx
    )

    ad = reg.require("wechat")
    await ad.project_outbound(wx)
    assert len(ad.outbound_batches) == 1
