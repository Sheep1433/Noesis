"""飞书配置、事件规范化、幂等和 OpenAPI 操作。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from noesis.domain.chat.delivery.channels import channel_bindings, channel_registry, route_inbound
from noesis.domain.chat.delivery.feishu.adapter import EventDeduplicator, FeishuChannelAdapter
from noesis.domain.chat.delivery.feishu.client import FeishuBotClient
from noesis.services.messaging_channel_service import MessagingChannelService
from server.api.user_settings_api import ChannelUpsertBody
from noesis.domain.chat.hitl.pending import PendingHitl, pending_hitl
from noesis.services.channels import feishu_runtime
from noesis.services.channels.feishu_runtime import _HitlPrompt, _hitl_card


def _event(*, event_id: str = "evt-1", message_id: str = "m-1", chat_type: str = "p2p", text: str = "你好", mentions=None):
    return {
        "header": {"event_id": event_id},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "message_id": message_id, "chat_id": "oc_chat", "chat_type": chat_type,
                "message_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False),
                "mentions": mentions or [],
            },
        },
    }


@pytest.mark.asyncio
async def test_normalize_p2p_and_reject_duplicate() -> None:
    adapter = FeishuChannelAdapter(deduplicator=EventDeduplicator())
    inbound = await adapter.normalize_inbound(_event())
    assert inbound is not None
    assert inbound.external_chat_id == "ou_user"
    assert inbound.raw["reply_chat_id"] == "oc_chat"
    assert await adapter.normalize_inbound(_event()) is None


@pytest.mark.asyncio
async def test_group_requires_mention_and_strips_key() -> None:
    adapter = FeishuChannelAdapter()
    assert await adapter.normalize_inbound(_event(chat_type="group")) is None
    inbound = await adapter.normalize_inbound(
        _event(event_id="evt-2", message_id="m-2", chat_type="group", text="@_user_1 帮我查", mentions=[{"key": "@_user_1"}])
    )
    assert inbound is not None
    assert inbound.text == "帮我查"


def test_feishu_registry_is_real_adapter() -> None:
    assert isinstance(channel_registry.require("feishu"), FeishuChannelAdapter)


def test_user_channel_schema_rejects_feishu_app_credentials() -> None:
    with pytest.raises(ValueError):
        ChannelUpsertBody.model_validate({
            "type": "feishu", "pairing_user_id": "ou_user",
            "app_id": "cli_forbidden", "app_secret": "forbidden",
        })


@pytest.mark.asyncio
async def test_feishu_binding_uses_open_id_without_user_app_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")
    channel_bindings.clear()
    item = MessagingChannelService.create_channel("u1", {
        "type": "feishu", "display_name": "飞书",
        "pairing_user_id": "ou_user", "pairing_chat_id": "oc_chat", "enabled": True,
    })
    raw = MessagingChannelService.channels_config_path("u1").read_text(encoding="utf-8")
    assert "app_id" not in raw
    assert "app_secret" not in raw
    assert "app_id" not in item
    assert "has_app_secret" not in item
    inbound = await FeishuChannelAdapter().normalize_inbound(_event(event_id="unique"))
    assert inbound is not None
    assert route_inbound(inbound).ok
    runtime = MessagingChannelService.get_runtime_channel("u1", item["channel_id"])
    assert runtime.pairing_user_id == "ou_user"


def test_two_noesis_users_resolve_to_their_own_feishu_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")
    channel_bindings.clear()
    MessagingChannelService.create_channel("u1", {
        "type": "feishu", "display_name": "飞书 A", "pairing_user_id": "ou_a", "enabled": True,
        "default_qa_type": "COMMON_QA",
    })
    MessagingChannelService.create_channel("u2", {
        "type": "feishu", "display_name": "飞书 B", "pairing_user_id": "ou_b", "enabled": True,
        "default_qa_type": "SUPER_AGENT_QA",
    })
    cfg_a = feishu_runtime._config_for_binding("u1", "ou_a")
    cfg_b = feishu_runtime._config_for_binding("u2", "ou_b")
    assert cfg_a is not None and cfg_a.user_id == "u1" and cfg_a.default_qa_type == "COMMON_QA"
    assert cfg_b is not None and cfg_b.user_id == "u2" and cfg_b.default_qa_type == "SUPER_AGENT_QA"
    assert feishu_runtime._config_for_binding("u1", "ou_b") is None


@pytest.mark.asyncio
async def test_feishu_client_auth_and_send() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_1"}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuBotClient("cli_123", "secret", http_client=http)
    result = await client.send_text("oc_chat", "hello")
    await http.aclose()
    assert result["message_id"] == "om_1"
    assert requests[-1].headers["authorization"] == "Bearer tenant-token"
    assert b"secret" not in requests[-1].content


def test_hitl_card_contains_opaque_token_without_tool_arguments() -> None:
    card = _hitl_card("opaque-token", {"action_requests": [{"name": "execute", "args": {"command": "secret command"}}]})
    raw = json.dumps(card, ensure_ascii=False)
    assert "opaque-token" in raw
    assert "execute" in raw
    assert "secret command" not in raw


@pytest.mark.asyncio
async def test_card_callback_rejects_other_open_id(monkeypatch: pytest.MonkeyPatch) -> None:
    feishu_runtime._hitl_prompts.clear()
    feishu_runtime._hitl_prompts["tok"] = _HitlPrompt("tok", "u1", "s1", "oc_1", "int_1", 1, time.monotonic() + 60)
    resume = AsyncMock()
    monkeypatch.setattr(feishu_runtime, "resume_channel_hitl", resume)
    cfg = SimpleNamespace(user_id="u1", pairing_user_id="ou_owner")
    raw = {"event": {"operator": {"open_id": "ou_other"}, "action": {"value": {"token": "tok", "decision": "approve"}}}}
    await feishu_runtime._handle_card(cfg, AsyncMock(), raw)
    resume.assert_not_awaited()
    assert "tok" in feishu_runtime._hitl_prompts


@pytest.mark.asyncio
async def test_card_callback_evicts_expired_hitl_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    feishu_runtime._hitl_prompts.clear()
    feishu_runtime._hitl_prompts["expired"] = _HitlPrompt(
        "expired", "u1", "s1", "oc_1", "int_1", 1, time.monotonic() - 1
    )
    resume = AsyncMock()
    monkeypatch.setattr(feishu_runtime, "resume_channel_hitl", resume)
    cfg = SimpleNamespace(user_id="u1", pairing_user_id="ou_owner")
    raw = {
        "event": {
            "operator": {"open_id": "ou_owner"},
            "action": {"value": {"token": "expired", "decision": "approve"}},
        }
    }

    await feishu_runtime._handle_card(cfg, AsyncMock(), raw)

    resume.assert_not_awaited()
    assert "expired" not in feishu_runtime._hitl_prompts


def test_hitl_prompt_store_evicts_expired_and_bounds_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.monotonic()
    feishu_runtime._hitl_prompts.clear()
    monkeypatch.setattr(feishu_runtime, "_HITL_PROMPT_MAX_ITEMS", 2)
    feishu_runtime._hitl_prompts["expired"] = _HitlPrompt(
        "expired", "u1", "s1", "oc_1", "int_1", 1, now - 1
    )

    for index in range(3):
        feishu_runtime._store_hitl_prompt(
            _HitlPrompt(
                f"token-{index}",
                "u1",
                "s1",
                "oc_1",
                f"int-{index}",
                1,
                now + 60 + index,
            )
        )

    assert set(feishu_runtime._hitl_prompts) == {"token-1", "token-2"}


@pytest.mark.asyncio
async def test_clarification_text_resumes_existing_run(monkeypatch: pytest.MonkeyPatch) -> None:
    pending_hitl.put(PendingHitl(
        interrupt_id="int_1", session_id="s1", user_id="u1", assistant_message_id="a1",
        expires_at=time.time() + 60, kind="clarification", action_requests=[{"name": "ask_user"}],
    ))
    result = SimpleNamespace(hitl_pending=False, hitl_payload=None, plain_text="完成")
    resume = AsyncMock(return_value=result)
    monkeypatch.setattr(feishu_runtime, "resume_channel_hitl", resume)
    client = AsyncMock()
    client.send_text.return_value = {"message_id": "om_1"}
    binding = SimpleNamespace(session_id="s1", user_id="u1")
    cfg = SimpleNamespace(user_id="u1")
    assert await feishu_runtime._try_clarification(cfg, client, binding, "oc_1", "补充内容")
    decisions = resume.await_args.kwargs["decisions"]
    assert decisions == [{"type": "respond", "message": "补充内容"}]
    pending_hitl.clear("s1")


@pytest.mark.asyncio
async def test_dispatch_message_routes_normalized_inbound_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FeishuChannelAdapter()
    inbound = await adapter.normalize_inbound(_event(event_id="route-once"))
    assert inbound is not None
    binding = SimpleNamespace(user_id="u1", session_id="s1")
    route_count = 0

    def route_once(_inbound):
        nonlocal route_count
        route_count += 1
        return SimpleNamespace(ok=True, binding=binding)

    cfg = SimpleNamespace(
        user_id="u1",
        channel_id="channel-1",
        pairing_chat_id="oc_chat",
        session_strategy="persistent",
        delivery_preference="silent",
        default_qa_type="SUPER_AGENT_QA",
    )
    monkeypatch.setattr(feishu_runtime, "route_inbound", route_once)
    monkeypatch.setattr(feishu_runtime, "_config_for_binding", lambda *_args: cfg)
    monkeypatch.setattr(
        feishu_runtime,
        "run_channel_agent",
        AsyncMock(
            return_value=SimpleNamespace(
                hitl_pending=False,
                hitl_payload=None,
                plain_text="",
            )
        ),
    )

    await feishu_runtime._dispatch_message(AsyncMock(), adapter, _event(event_id="route-once-2"))

    assert route_count == 1
