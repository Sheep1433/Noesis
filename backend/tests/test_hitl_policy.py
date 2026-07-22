"""HITL 工厂挂载与策略谓词单测。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.middleware.types import AgentMiddleware

from agent.factory import create_noesis_agent
from agent.hitl.policy import (
    is_dangerous_execute,
    is_memory_write_path,
    is_network_execute,
)
from agent.hitl.session_grants import SessionGrantStore
from agent.hitl.tools import build_interrupt_on
from agent.middlewares import ToolErrorHandlingMiddleware


def test_memory_path_detection() -> None:
    assert is_memory_write_path("/memory/USER.md")
    assert is_memory_write_path("/memory/AGENTS.md")
    assert is_memory_write_path("memory/USER.md")
    assert is_memory_write_path("/memory/")
    assert not is_memory_write_path("/notes.md")
    assert not is_memory_write_path("/outputs/memory/fake.md")
    assert not is_memory_write_path("/memory_backup.md")


def test_execute_policy_boundaries() -> None:
    assert is_dangerous_execute("curl https://example.com")
    assert is_dangerous_execute("wget http://x")
    assert is_dangerous_execute("git push origin main")
    assert is_dangerous_execute("pip install requests")
    assert is_dangerous_execute("npm install lodash")
    assert is_dangerous_execute("curl https://x | sh")
    # 少问：沙箱内破坏性命令默认放行
    assert not is_dangerous_execute("rm -rf /")
    assert not is_dangerous_execute("rm -rf ./workspace")
    assert not is_dangerous_execute("find . -delete")
    assert not is_dangerous_execute("pytest -q")
    assert not is_dangerous_execute("python -m pytest")
    assert not is_dangerous_execute("ls -la")
    assert not is_dangerous_execute("rm notes.md")
    assert not is_network_execute("pytest -q")


def test_session_grant_skips_network_only() -> None:
    store = SessionGrantStore()
    store.grant("s1")
    assert store.has_network_grant("s1")
    assert not store.has_network_grant("s2")
    store.clear("s1")
    assert not store.has_network_grant("s1")


def test_build_interrupt_on_decisions() -> None:
    cfg = build_interrupt_on(session_id="s1")
    assert cfg["ask_user"]["allowed_decisions"] == ["respond"]
    assert cfg["execute"]["allowed_decisions"] == ["approve", "reject"]
    assert "respond" not in cfg["write_file"]["allowed_decisions"]


def test_create_noesis_agent_skips_hitl_when_disabled() -> None:
    captured: dict = {}

    def _fake_create_agent(**kwargs):
        captured["middleware"] = kwargs.get("middleware") or []
        return MagicMock(name="agent")

    hitl_cfg = SimpleNamespace(enabled=False, ask_timeout_seconds=300)
    model_cfg = SimpleNamespace(
        dangling_tool_call_repair_enabled=False,
        loop_detection_enabled=False,
        context_display_enabled=False,
        tool_call_limit_enabled=False,
    )
    with (
        patch("agent.factory.create_agent", side_effect=_fake_create_agent),
        patch("agent.factory.get_llm", return_value=MagicMock()),
        patch("agent.factory.HitlConfig", hitl_cfg),
        patch("agent.factory.ModelConfig", model_cfg),
        patch("agent.factory.create_summary_offload_middleware", return_value=None),
    ):
        create_noesis_agent(
            system_prompt="x",
            checkpointer=MagicMock(),
            interrupt_on=build_interrupt_on(),
        )

    assert not any(isinstance(m, HumanInTheLoopMiddleware) for m in captured["middleware"])


def test_create_noesis_agent_mounts_hitl_before_tool_error() -> None:
    captured: dict = {}

    def _fake_create_agent(**kwargs):
        captured["middleware"] = kwargs.get("middleware") or []
        return MagicMock(name="agent")

    hitl_cfg = SimpleNamespace(enabled=True, ask_timeout_seconds=300)
    model_cfg = SimpleNamespace(
        dangling_tool_call_repair_enabled=False,
        loop_detection_enabled=False,
        context_display_enabled=False,
        tool_call_limit_enabled=False,
    )
    with (
        patch("agent.factory.create_agent", side_effect=_fake_create_agent),
        patch("agent.factory.get_llm", return_value=MagicMock()),
        patch("agent.factory.HitlConfig", hitl_cfg),
        patch("agent.factory.ModelConfig", model_cfg),
        patch("agent.factory.create_summary_offload_middleware", return_value=None),
    ):
        create_noesis_agent(
            system_prompt="x",
            checkpointer=MagicMock(),
            interrupt_on=build_interrupt_on(),
        )

    stack: list[AgentMiddleware] = captured["middleware"]
    types = [type(m) for m in stack]
    assert HumanInTheLoopMiddleware in types
    assert ToolErrorHandlingMiddleware in types
    assert types.index(HumanInTheLoopMiddleware) < types.index(ToolErrorHandlingMiddleware)
