"""首批 A 类 handler：注册与输出。"""
from __future__ import annotations

import importlib

import pytest

from noesis.chat.commands import registry as reg
from noesis.chat.commands.handlers import agents, help, model, skills, status
from noesis.chat.commands.registry import dispatch, list_commands
from noesis.chat.delivery.channels import InboundMessage

_HANDLER_MODULES = [help, skills, agents, model, status]


def _msg(text: str, *, user_id: str = "u1") -> InboundMessage:
    return InboundMessage(channel_type="test", external_chat_id="c1", text=text, user_id=user_id)


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
