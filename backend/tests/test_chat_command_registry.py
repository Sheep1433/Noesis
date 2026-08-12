"""命令注册表 + 分发器：命中 / 未命中 / 未知命令 / 非斜杠放行。"""
from __future__ import annotations

import pytest

from noesis.chat.commands import registry as reg
from noesis.chat.commands.registry import (
    dispatch,
    list_commands,
    list_command_descriptions,
    command,
)
from noesis.chat.commands.result import CommandResult
from noesis.chat.delivery.channels import InboundMessage


def _msg(text: str) -> InboundMessage:
    return InboundMessage(channel_type="test", external_chat_id="c1", text=text, user_id="u1")


@pytest.fixture
def isolated_registry() -> None:
    """每个用例隔离注册表，避免 handler 交叉污染。"""
    saved = dict(reg._registry)
    reg._registry.clear()
    yield
    reg._registry.clear()
    reg._registry.update(saved)


async def _echo(ev: InboundMessage) -> CommandResult:
    return CommandResult(handled=True, text=f"echo:{ev.command_args()}")


async def test_non_sllash_passthrough(isolated_registry: None) -> None:
    result = await dispatch(_msg("帮我查天气"))
    assert result.handled is False
    assert result.text == ""


async def test_empty_text_passthrough(isolated_registry: None) -> None:
    result = await dispatch(_msg(""))
    assert result.handled is False


async def test_unknown_command_handled_with_hint(isolated_registry: None) -> None:
    result = await dispatch(_msg("/typo"))
    assert result.handled is True
    assert "/typo" in result.text
    assert "/help" in result.text


async def test_registered_command_dispatched(isolated_registry: None) -> None:
    command("echo")(_echo)
    result = await dispatch(_msg("/echo hello world"))
    assert result.handled is True
    assert result.text == "echo:hello world"


def test_command_name_parsing(isolated_registry: None) -> None:
    """斜杠命令在 InboundMessage 唯一解析点解析。"""
    assert _msg("/help x y").command_name() == "help"
    assert _msg("/help x y").command_args() == "x y"
    assert _msg("/reset").command_name() == "reset"
    assert _msg("/reset").command_args() == ""
    assert _msg("not a command").command_name() is None
    assert _msg("/").command_name() is None


def test_list_commands_sorted(isolated_registry: None) -> None:
    command("zeta")(_echo)
    command("alpha")(_echo)
    assert list_commands() == ["alpha", "zeta"]


def test_duplicate_registration_rejected(isolated_registry: None) -> None:
    command("dup")(_echo)
    with pytest.raises(ValueError, match="already registered"):
        command("dup")(_echo)


def test_control_commands_are_reserved() -> None:
    assert "help" in reg.CONTROL_COMMANDS
    assert "skills" in reg.CONTROL_COMMANDS
    assert "reset" in reg.CONTROL_COMMANDS


def test_command_description_recorded(isolated_registry: None) -> None:
    command("alpha", description="alpha cmd")(_echo)
    command("zeta", description="zeta cmd")(_echo)
    descs = dict(list_command_descriptions())
    assert descs["alpha"] == "alpha cmd"
    assert descs["zeta"] == "zeta cmd"


def test_command_default_description_empty(isolated_registry: None) -> None:
    command("plain")(_echo)
    descs = dict(list_command_descriptions())
    assert descs["plain"] == ""
