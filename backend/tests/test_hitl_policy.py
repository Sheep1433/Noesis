"""HITL policy predicates and runtime inventory ordering."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain.agents.middleware import HumanInTheLoopMiddleware

from noesis.factory import create_noesis_agent
from noesis.agents.guardrails.policy import is_dangerous_execute, is_network_execute, memory_write_when
from noesis.agents.guardrails.session_grants import SessionGrantStore
from noesis.agents.tools.ask_user import build_interrupt_on


def _tool_req(path: str) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"args": {"file_path": path}})


def test_memory_write_when_gates_on_guard_whitelist() -> None:
    """审批只对 guard 白名单内的路径触发：白名单外写入直接放行给 guard 拒绝。"""
    assert memory_write_when(_tool_req("/memory/USER.md"))
    assert memory_write_when(_tool_req("/memory/AGENTS.md"))
    assert memory_write_when(_tool_req("memory/USER.md"))
    assert memory_write_when(_tool_req("/memory/preference/document-format.md"))
    # 白名单外：根级任意文件（如研究产物）不触发审批——guard 必然拒绝
    assert not memory_write_when(_tool_req("/memory/agent_evaluation_research.md"))
    assert not memory_write_when(_tool_req("/memory/"))
    # 非 memory 路径不触发
    assert not memory_write_when(_tool_req("/notes.md"))
    assert not memory_write_when(_tool_req("/outputs/memory/fake.md"))
    assert not memory_write_when(_tool_req("/memory_backup.md"))


def test_execute_policy_boundaries() -> None:
    assert is_dangerous_execute("curl https://example.com")
    assert is_dangerous_execute("git push origin main")
    assert is_dangerous_execute("pip install requests")
    assert not is_dangerous_execute("rm -rf ./workspace")
    assert not is_dangerous_execute("pytest -q")
    assert not is_network_execute("pytest -q")


def test_session_grant_skips_network_only() -> None:
    store = SessionGrantStore()
    store.grant("s1")
    assert store.has_network_grant("s1")
    assert not store.has_network_grant("s2")
    store.clear("s1")
    assert not store.has_network_grant("s1")


def test_build_interrupt_on_decisions() -> None:
    config = build_interrupt_on(session_id="s1")
    assert config["ask_user"]["allowed_decisions"] == ["respond"]
    assert config["execute"]["allowed_decisions"] == ["approve", "reject"]
    assert "respond" not in config["write_file"]["allowed_decisions"]


def _captured_stack(*, hitl_enabled: bool):
    captured: dict = {}

    def fake_create_agent(**kwargs):
        captured["middleware"] = kwargs.get("middleware") or []
        return MagicMock(name="agent")

    model_config = SimpleNamespace(
        context_display_enabled=False,
        summarization_enabled=False,
        max_retries=0,
        governor_tool_calls_enabled=False,
        governor_loop_enabled=False,
    )
    with (
        patch("noesis.factory.create_agent", side_effect=fake_create_agent),
        patch("noesis.factory.HitlConfig", SimpleNamespace(enabled=hitl_enabled)),
        patch("noesis.factory.ModelConfig", model_config),
    ):
        create_noesis_agent(
            system_prompt="x",
            checkpointer=MagicMock(),
            profile="COMMON_QA",
            model=MagicMock(),
            interrupt_on=build_interrupt_on(),
        )
    return captured["middleware"]


def test_create_noesis_agent_skips_hitl_when_disabled() -> None:
    assert not any(isinstance(item, HumanInTheLoopMiddleware) for item in _captured_stack(hitl_enabled=False))


def test_hitl_is_innermost() -> None:
    names = [type(item).__name__ for item in _captured_stack(hitl_enabled=True)]
    assert "HumanInTheLoopMiddleware" in names
    assert names[-1] == "HumanInTheLoopMiddleware"
