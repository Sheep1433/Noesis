"""通道健康、测试命令、隔离与敏感值安全。"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from noesis.domain.chat.delivery.channel_health import channel_health
from noesis.domain.chat.delivery.channels import channel_bindings
from noesis.errors.exceptions import ConflictException
from noesis.services.channel_operations_service import ChannelOperationsService, _last_command
from noesis.services.messaging_channel_service import MessagingChannelService
from noesis.services import messaging_channel_service


@pytest.fixture(autouse=True)
def channel_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    channel_health.clear()
    channel_bindings.clear()
    _last_command.clear()


def _create(user_id="u1", **overrides):
    payload = {
        "type": "telegram", "display_name": "Bot", "bot_token": "123456:secret-token",
        "pairing_chat_id": "42", "enabled": True, "default_qa_type": "SUPER_AGENT_QA",
        "session_strategy": "persistent", "delivery_preference": "reply",
    }
    payload.update(overrides)
    return MessagingChannelService.create_channel(user_id, payload)


def test_channel_token_is_encrypted_and_public_view_is_masked() -> None:
    item = _create()
    raw = MessagingChannelService.channels_config_path("u1").read_text(encoding="utf-8")
    assert "123456:secret-token" not in raw
    assert '"bot_token": "enc:' in raw
    assert item["bot_token_masked"].endswith("oken")
    assert "secret-token" not in repr(item)


@pytest.mark.asyncio
async def test_connection_and_delivery_use_adapter_without_chat_side_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _create()
    sent = []

    class FakeClient:
        def __init__(self, token, **_kwargs):
            assert token == "123456:secret-token"

        async def get_me(self):
            return {"id": 1}

        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))
            return {"message_id": 9}

        async def aclose(self):
            return None

    monkeypatch.setattr("noesis.services.channel_operations_service.TelegramBotClient", FakeClient)
    save_message = AsyncMock()
    monkeypatch.setattr("noesis.services.chat_service.ChatService.save_message", save_message)
    connection = await ChannelOperationsService.test_connection("u1", item["channel_id"])
    delivery = await ChannelOperationsService.test_delivery("u1", item["channel_id"])
    assert connection["status"] == "healthy"
    assert delivery["status"] == "delivered"
    assert sent == [("42", "Noesis 通道测试成功。你可以返回设置页继续配置。")]
    save_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_feishu_connection_and_delivery_use_feishu_client(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _create(
        type="feishu", display_name="飞书", bot_token=None,
        pairing_user_id="ou_1", pairing_chat_id="oc_1",
    )
    sent = []

    class FakeFeishuClient:
        def __init__(self, app_id, app_secret, **_kwargs):
            assert (app_id, app_secret) == ("cli_123", "app-secret")
        async def get_bot_info(self): return {"bot": {"open_id": "ou_bot"}}
        async def send_text(self, chat_id, text):
            sent.append((chat_id, text))
            return {"message_id": "om_1"}
        async def aclose(self): return None

    monkeypatch.setattr("noesis.services.channel_operations_service.FeishuBotClient", FakeFeishuClient)
    monkeypatch.setattr(
        "noesis.services.channel_operations_service.MessagingConfig",
        type("Config", (), {"feishu_app_id": "cli_123", "feishu_app_secret": "app-secret"})(),
    )
    assert (await ChannelOperationsService.test_connection("u1", item["channel_id"]))["status"] == "healthy"
    assert (await ChannelOperationsService.test_delivery("u1", item["channel_id"]))["status"] == "delivered"
    assert sent == [("oc_1", "Noesis 通道测试成功。你可以返回设置页继续配置。")]


@pytest.mark.asyncio
async def test_feishu_operation_reports_shared_service_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _create(type="feishu", bot_token=None, pairing_user_id="ou_1")
    monkeypatch.setattr(
        "noesis.services.channel_operations_service.MessagingConfig",
        type("Config", (), {"feishu_app_id": "", "feishu_app_secret": ""})(),
    )
    with pytest.raises(ConflictException) as error:
        await ChannelOperationsService.test_connection("u1", item["channel_id"])
    assert error.value.data["code"] == "service_unavailable"


@pytest.mark.asyncio
async def test_disabled_or_unpaired_channel_rejects_test_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    disabled = _create(enabled=False)
    with pytest.raises(ConflictException) as disabled_error:
        await ChannelOperationsService.test_delivery("u1", disabled["channel_id"])
    assert disabled_error.value.data["code"] == "channel_disabled"

    unpaired = _create(user_id="u2", pairing_chat_id=None)
    with pytest.raises(ConflictException) as unpaired_error:
        await ChannelOperationsService.test_delivery("u2", unpaired["channel_id"])
    assert unpaired_error.value.data["code"] == "channel_unpaired"


@pytest.mark.asyncio
async def test_delivery_rejects_cleared_telegram_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _create()
    MessagingChannelService.update_channel(
        "u1",
        item["channel_id"],
        {"bot_token_action": "clear"},
    )

    class UnexpectedClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("缺少 Token 时不应构造 Telegram 客户端")

    monkeypatch.setattr(
        "noesis.services.channel_operations_service.TelegramBotClient",
        UnexpectedClient,
    )

    with pytest.raises(ConflictException) as error:
        await ChannelOperationsService.test_delivery("u1", item["channel_id"])

    assert error.value.data["code"] == "credential_missing"


@pytest.mark.asyncio
async def test_test_command_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _create()

    class FakeClient:
        def __init__(self, *_args, **_kwargs): pass
        async def get_me(self): return {"id": 1}
        async def aclose(self): return None

    monkeypatch.setattr("noesis.services.channel_operations_service.TelegramBotClient", FakeClient)
    await ChannelOperationsService.test_connection("u1", item["channel_id"])
    with pytest.raises(ConflictException) as error:
        await ChannelOperationsService.test_connection("u1", item["channel_id"])
    assert error.value.data["code"] == "rate_limited"


def test_health_read_model_is_user_scoped_and_redacted() -> None:
    item_a = _create("u-a")
    _create("u-b")
    channel_health.report_status("u-a", item_a["channel_id"], "unavailable", "连接失败", error_category="connection")
    health_a = MessagingChannelService.list_channels("u-a")[0]["health"]
    health_b = MessagingChannelService.list_channels("u-b")[0]["health"]
    assert health_a["status"] == "unavailable"
    assert health_b["status"] == "unknown"
    assert "token" not in json.dumps(health_a).lower()


def test_disabling_channel_removes_inbound_binding_and_runtime_worker() -> None:
    item = _create()
    assert channel_bindings.resolve("telegram", "42") is not None

    MessagingChannelService.update_channel(
        "u1",
        item["channel_id"],
        {"enabled": False, "bot_token_action": "keep"},
    )

    assert channel_bindings.resolve("telegram", "42") is None
    assert MessagingChannelService.iter_enabled_runtime("telegram", user_id="u1") == []


def test_sync_all_bindings_reads_each_user_config_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create("u1")
    users_root = tmp_path / "users"
    monkeypatch.setattr(messaging_channel_service, "_USERS_ROOT", users_root)
    original_load = messaging_channel_service._load_raw
    load_count = 0

    def counting_load(user_id):
        nonlocal load_count
        load_count += 1
        return original_load(user_id)

    monkeypatch.setattr(messaging_channel_service, "_load_raw", counting_load)

    MessagingChannelService.sync_all_bindings()

    assert load_count == 1
