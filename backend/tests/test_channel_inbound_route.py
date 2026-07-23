"""入站路由：未配对拒绝。"""
from __future__ import annotations

from domain.chat.delivery.channels import ChannelBinding, ChannelBindingStore, InboundMessage
from domain.chat.delivery.channels import route_inbound


def test_unpaired_inbound_rejected() -> None:
    store = ChannelBindingStore()
    msg = InboundMessage(
        channel_type="telegram",
        external_chat_id="tg-99",
        text="hi",
    )
    result = route_inbound(msg, store=store)
    assert result.ok is False
    assert result.reject_reason == "unpaired"


def test_paired_inbound_ok() -> None:
    store = ChannelBindingStore()
    store.put(
        ChannelBinding(
            user_id="u1",
            channel_type="telegram",
            external_chat_id="tg-1",
            session_id="s1",
        )
    )
    msg = InboundMessage(
        channel_type="telegram",
        external_chat_id="tg-1",
        text="hi",
    )
    result = route_inbound(msg, store=store)
    assert result.ok is True
    assert result.binding is not None
    assert result.binding.session_id == "s1"
