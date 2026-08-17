"""首批 A 类 handler：注册与输出。"""
from __future__ import annotations

import importlib

import pytest

from noesis.chat.commands import registry as reg
from noesis.chat.commands.handlers import agents, compact, help, model, new, skills, status
from noesis.chat.commands.registry import dispatch, list_commands
from noesis.chat.delivery.channels import ChannelBinding, InboundMessage, channel_bindings

_HANDLER_MODULES = [help, skills, agents, model, status, compact, new]


def _msg(text: str, *, user_id: str = "u1", channel: str = "test") -> InboundMessage:
    return InboundMessage(channel_type=channel, external_chat_id="c1", text=text, user_id=user_id)


@pytest.fixture(autouse=True)
def _handlers_registered() -> None:
    """清空 registry 后 reload 各 handler 模块，强制 @command 重新注册。

    模块已在文件顶部 import（注册一次）；此处先 clear 再 reload，
    reload 重跑装饰器到空 registry，不会触发重复注册。
    """
    saved = dict(reg._registry)
    reg._registry.clear()
    for mod in _HANDLER_MODULES:
        importlib.reload(mod)
    yield
    reg._registry.clear()
    reg._registry.update(saved)


def test_all_commands_registered() -> None:
    assert {"help", "skills", "agents", "model", "status"} <= set(list_commands())


async def test_help_lists_commands() -> None:
    result = await dispatch(_msg("/help"))
    assert result.handled is True
    assert "/help" in result.text
    assert "/skills" in result.text


async def test_skills_lists_packages() -> None:
    result = await dispatch(_msg("/skills"))
    assert result.handled is True
    # 扫描实际 skills_root；至少应包含 baoyu-url-to-markdown（仓库自带）
    assert "baoyu-url-to-markdown" in result.text or "未安装" in result.text


async def test_agents_lists_qa_type() -> None:
    result = await dispatch(_msg("/agents"))
    assert result.handled is True
    assert "SUPER_AGENT_QA" in result.text


async def test_model_lists_default() -> None:
    result = await dispatch(_msg("/model"))
    assert result.handled is True
    assert "默认模型" in result.text


async def test_status_no_run_manager_graceful() -> None:
    # 测试环境无 DB wiring，run_manager 可能不可用或为空
    result = await dispatch(_msg("/status"))
    assert result.handled is True
    # 降级路径或空活跃 run 均可接受
    assert "状态" in result.text or "活跃 run" in result.text or "无活跃" in result.text


async def test_status_with_injected_run_manager() -> None:
    """注入 run_manager provider 后 /status 返回活跃 run。"""
    from types import SimpleNamespace

    from noesis.chat.commands import runtime as cmd_rt
    from noesis.chat.runs.models import RunStatus

    fake_handle = SimpleNamespace(
        run_id="r-1", user_id="u1", status=RunStatus.HITL_PENDING,
    )
    fake_rm = SimpleNamespace(list_active_for_user=lambda uid: [fake_handle])
    prev = cmd_rt._run_manager_provider
    cmd_rt.set_run_manager_provider(lambda: fake_rm)
    try:
        result = await dispatch(_msg("/status", user_id="u1"))
        assert result.handled is True
        assert "r-1" in result.text
        assert "hitl_pending" in result.text
        assert "HITL 待审批" in result.text
    finally:
        cmd_rt._run_manager_provider = prev


def test_control_commands_reserved_against_skill_names() -> None:
    """skill 命令 fallback 须先校验不在 CONTROL_COMMANDS。"""
    assert "help" in reg.CONTROL_COMMANDS
    assert "skills" in reg.CONTROL_COMMANDS
    assert "new" in reg.CONTROL_COMMANDS


def test_command_channels_filter() -> None:
    """声明 channels 的命令只在该通道的命令发现中出现；不传 channel 返回全部。"""
    all_names = {n for n in list_commands()}
    assert "new" in all_names  # 不传 channel 兼容旧调用，返回全部
    web_names = {n for n, _ in reg.list_command_descriptions(channel="web")}
    tg_names = {n for n, _ in reg.list_command_descriptions(channel="telegram")}
    assert "new" not in web_names  # web 不暴露 /new（用「新对话」按钮）
    assert "new" in tg_names
    # 全通道命令（help/skills 等）在两个通道都出现
    assert "help" in web_names and "help" in tg_names


async def test_new_web_channel_passthrough() -> None:
    """web 通道打 /new → dispatch 放行（handled=False），不当命令执行。"""
    result = await dispatch(_msg("/new", channel="web"))
    assert result.handled is False  # 放行进 Agent，不当命令


async def test_new_no_binding() -> None:
    """未配对通道（无 binding）→ 提示未配对。"""
    channel_bindings.clear()
    try:
        result = await dispatch(_msg("/new", channel="telegram"))
        assert result.handled is True
        assert "未配对" in result.text
    finally:
        channel_bindings.clear()  # 还原全局 binding 状态，避免测试间泄漏


async def test_new_factory_unavailable() -> None:
    """有 binding 但 session_factory 未注入 → 降级提示不可用。"""
    from noesis.chat.commands import runtime as cmd_rt

    channel_bindings.clear()
    channel_bindings.put(
        ChannelBinding(
            user_id="u1", channel_type="telegram",
            external_chat_id="c1", session_id="old-session-id",
        )
    )
    prev = cmd_rt._session_factory_provider
    cmd_rt._session_factory_provider = None
    try:
        result = await dispatch(_msg("/new", channel="telegram"))
        assert result.handled is True
        assert "不可用" in result.text
        # 未重绑
        assert channel_bindings.resolve("telegram", "c1").session_id == "old-session-id"
    finally:
        cmd_rt._session_factory_provider = prev


async def test_new_rebinds_binding() -> None:
    """有 binding + 注入 factory → 建新 session 并重绑 binding，旧 session_id 保留可追溯。"""
    from noesis.chat.commands import runtime as cmd_rt

    channel_bindings.clear()
    channel_bindings.put(
        ChannelBinding(
            user_id="u1", channel_type="telegram",
            external_chat_id="c1", session_id="old-session-id",
        )
    )

    async def _fake_factory(user_id, title=None, parent_id=None):
        return "new-session-id"

    prev = cmd_rt._session_factory_provider
    cmd_rt.set_session_factory_provider(lambda: _fake_factory)
    try:
        result = await dispatch(_msg("/new", channel="telegram"))
        assert result.handled is True
        assert "新会话" in result.text
        assert "old-sess" in result.text  # 旧 session 提示保留
        new_binding = channel_bindings.resolve("telegram", "c1")
        assert new_binding.session_id == "new-session-id"
        assert new_binding.user_id == "u1"
        assert new_binding.channel_type == "telegram"
        assert new_binding.external_chat_id == "c1"
    finally:
        cmd_rt._session_factory_provider = prev
        channel_bindings.clear()
