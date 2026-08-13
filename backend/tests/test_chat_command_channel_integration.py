"""CLI 与 Telegram 通道接入统一命令层：命中则短路、不启动 Agent。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.chat.commands import registry as reg
from noesis.chat.commands.registry import dispatch
from noesis.chat.delivery.channels import InboundMessage


@pytest.fixture(autouse=True)
def _handlers_registered() -> None:
    import importlib

    saved = dict(reg._registry)
    reg._registry.clear()
    for name in ("help", "skills", "agents", "model", "status"):
        importlib.reload(
            importlib.import_module(f"noesis.chat.commands.handlers.{name}")
        )
    yield
    reg._registry.clear()
    reg._registry.update(saved)


# --- CLI -----------------------------------------------------------------

async def test_cli_command_dispatched_returns_reply() -> None:
    """CLI /help → dispatch 命中控制命令，返回 ephemeral 回复文本。"""
    from noesis.chat.commands.registry import dispatch
    from noesis.chat.delivery.channels import InboundMessage

    inbound = InboundMessage(
        channel_type="cli", external_chat_id="cli-local", text="/help", user_id="cli-user",
    )
    result = await dispatch(inbound)
    assert result.handled is True
    assert not result.rewrite_request
    assert "/help" in result.text
    assert "/skills" in result.text


async def test_cli_skill_command_rewrites_to_agent_run() -> None:
    """CLI /baoyu-url-to-markdown <问题> → dispatch 返回 rewrite_request。"""
    from noesis.chat.commands.registry import dispatch
    from noesis.chat.delivery.channels import InboundMessage

    inbound = InboundMessage(
        channel_type="cli",
        external_chat_id="cli-local",
        text="/baoyu-url-to-markdown 抓取 https://example.com",
        user_id="cli-user",
    )
    result = await dispatch(inbound)
    assert result.handled is True
    assert result.rewrite_request is not None
    assert result.rewrite_request.enabled_skills == ["baoyu-url-to-markdown"]
    assert result.rewrite_request.query == "抓取 https://example.com"


async def test_cli_non_command_passes_through() -> None:
    from noesis.chat.commands.registry import dispatch
    from noesis.chat.delivery.channels import InboundMessage

    inbound = InboundMessage(
        channel_type="cli", external_chat_id="cli-local", text="帮我查天气", user_id="cli-user",
    )
    result = await dispatch(inbound)
    assert result.handled is False


# --- Telegram ------------------------------------------------------------

async def test_telegram_command_short_circuits_before_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram 入站 /help → dispatch 命中 → client.send_message 被调，run_channel_agent 不被调。"""
    import noesis.services.channels.telegram_runtime as rt

    inbound = InboundMessage(
        channel_type="telegram",
        external_chat_id="tg-1",
        text="/help",
        user_id="u1",
    )

    adapter = MagicMock()
    adapter.normalize_inbound = AsyncMock(return_value=inbound)

    client = MagicMock()
    client.send_message = AsyncMock()

    cfg = SimpleNamespace(
        user_id=1, channel_id="c1", delivery_preference="reply",
        session_strategy="reuse", default_qa_type="SUPER_AGENT_QA",
    )

    # route_inbound 返回已配对 binding
    monkeypatch.setattr(
        rt, "route_inbound",
        lambda msg: SimpleNamespace(ok=True, binding=SimpleNamespace(
            user_id="1", session_id="s1", channel_type="telegram",
            external_chat_id="tg-1", thread_id=None,
        )),
    )
    # 无 pending hitl
    monkeypatch.setattr(rt.pending_hitl, "get", lambda _sid: None)
    # 跳过 setMyCommands 热加载检查
    monkeypatch.setattr(rt, "_maybe_refresh_bot_commands", AsyncMock())
    # run_channel_agent 不应被调
    run_agent_mock = AsyncMock()
    monkeypatch.setattr(rt, "run_channel_agent", run_agent_mock)
    monkeypatch.setattr(rt.MessagingChannelService, "iter_enabled_runtime", MagicMock(return_value=iter([])))

    await rt._handle_message(cfg, client, adapter, {"message": {"text": "/help"}})

    client.send_message.assert_awaited_once()
    sent_args = client.send_message.call_args
    assert "tg-1" == sent_args.args[0]
    assert "/help" in sent_args.args[1]
    run_agent_mock.assert_not_awaited()


async def test_telegram_non_command_proceeds_to_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """非命令消息 → dispatch 未命中 → 走 run_channel_agent。"""
    import noesis.services.channels.telegram_runtime as rt
    from noesis.chat.delivery.channels import InboundMessage

    inbound = InboundMessage(
        channel_type="telegram", external_chat_id="tg-1", text="帮我查天气", user_id="u1",
    )
    adapter = MagicMock()
    adapter.normalize_inbound = AsyncMock(return_value=inbound)
    client = MagicMock()
    client.send_message = AsyncMock()
    cfg = SimpleNamespace(
        user_id=1, channel_id="c1", delivery_preference="reply",
        session_strategy="reuse", default_qa_type="SUPER_AGENT_QA",
    )
    monkeypatch.setattr(rt, "route_inbound", lambda msg: SimpleNamespace(
        ok=True, binding=SimpleNamespace(
            user_id="1", session_id="s1", channel_type="telegram",
            external_chat_id="tg-1", thread_id=None,
        )))
    monkeypatch.setattr(rt.pending_hitl, "get", lambda _sid: None)
    monkeypatch.setattr(rt, "_maybe_refresh_bot_commands", AsyncMock())
    run_agent_mock = AsyncMock(return_value=SimpleNamespace(final_text="ok", finish_reason="stop"))
    monkeypatch.setattr(rt, "run_channel_agent", run_agent_mock)
    monkeypatch.setattr(rt.MessagingChannelService, "iter_enabled_runtime", MagicMock(return_value=iter([])))
    # _after_channel_result 会用到 client，但不影响 run_agent 被调的断言
    monkeypatch.setattr(rt, "_after_channel_result", AsyncMock())

    await rt._handle_message(cfg, client, adapter, {"message": {"text": "帮我查天气"}})

    run_agent_mock.assert_awaited_once()


# --- Telegram 命令发现 ----------------------------------------------------

def test_build_bot_commands_merges_control_and_skills() -> None:
    """命令菜单 = 控制命令 + skill 命令，控制命令保留字优先。"""
    import noesis.services.channels.telegram_runtime as rt

    cmds = rt._build_bot_commands()
    names = [c["command"] for c in cmds]
    # 控制命令
    assert "help" in names and "skills" in names and "status" in names
    # skill 命令（仓库自带）
    assert "baoyu-url-to-markdown" in names
    # 每条带描述且 command 不带 /
    for c in cmds:
        assert "/" not in c["command"]
        assert c["description"]


async def test_set_my_commands_called_on_poll_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """TelegramBotClient.set_my_commands 封装 setMyCommands API。"""
    from noesis.chat.delivery.telegram.client import TelegramBotClient

    client = TelegramBotClient.__new__(TelegramBotClient)
    client._client = MagicMock()
    client._masked = "bot***"
    client._base = "https://api.telegram.org/bot"
    call_args: dict = {}
    async def fake_call(method, payload=None):
        call_args["method"] = method
        call_args["payload"] = payload
        return True
    client._call = fake_call  # type: ignore[assignment]
    await client.set_my_commands([{"command": "help", "description": "列出命令"}])
    assert call_args["method"] == "setMyCommands"
    assert call_args["payload"]["commands"][0]["command"] == "help"
