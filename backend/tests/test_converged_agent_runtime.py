"""Deterministic contracts for the new flat Agent runtime middleware stack.

The old five-owner kernel (RuntimeTelemetry / RunGovernor / ContextLifecycle /
ModelExecution / ToolExecution) has been retired. Behaviour now lives in the
self-contained middleware under ``noesis/middleware/`` (each with its own unit
test file). This file holds only stack-level contracts that span more than one
middleware: hook ordering, inventory/ordering, and the consolidated config
shape.
"""

from __future__ import annotations

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from noesis.factory import build_middleware_inventory, middleware_inventory


def test_inventory_has_single_kernel_in_spec_order() -> None:
    stack = build_middleware_inventory(profile="COMMON_QA")
    assert [type(item).__name__ for item in stack] == [
        "ToolResultBudgetMiddleware",
        "ToolFailureMiddleware",
        "DynamicContextMiddleware",
        "DurableContextMiddleware",
        "SnipMiddleware",
        "MicroCompactionMiddleware",
        "PatchToolCallsMiddleware",
        "SafeModelRetryMiddleware",
    ]


def test_langchain_hook_contract_is_before_wrap_after() -> None:
    calls: list[str] = []

    def make_middleware(name: str):
        class Recorder(AgentMiddleware):
            def before_model(self, state, runtime):
                calls.append(f"before:{name}")

            def wrap_model_call(self, request, handler):
                calls.append(f"wrap-enter:{name}")
                result = handler(request)
                calls.append(f"wrap-exit:{name}")
                return result

            def after_model(self, state, runtime):
                calls.append(f"after:{name}")

        Recorder.__name__ = f"Recorder_{name}"
        return Recorder()

    agent = create_agent(
        model=FakeListChatModel(responses=["ok"]),
        tools=[],
        system_prompt="test",
        checkpointer=InMemorySaver(),
        middleware=[make_middleware("a"), make_middleware("b"), make_middleware("c")],
    )
    agent.invoke({"messages": [HumanMessage(content="hi")]}, {"configurable": {"thread_id": "hook-test"}})
    assert calls == [
        "before:a",
        "before:b",
        "before:c",
        "wrap-enter:a",
        "wrap-enter:b",
        "wrap-enter:c",
        "wrap-exit:c",
        "wrap-exit:b",
        "wrap-exit:a",
        "after:c",
        "after:b",
        "after:a",
    ]


def test_inventory_has_no_legacy_loop_or_tool_call_limit_middleware() -> None:
    """旧 LoopDetection registry、五-owner kernel 与重复 ToolCallLimit 装配已删除。"""
    names = {entry.name for entry in middleware_inventory()}
    assert "LoopDetectionMiddleware" not in names
    assert "ToolCallLimitMiddleware" not in names
    assert "RunGovernorMiddleware" not in names
    assert "RuntimeTelemetryMiddleware" not in names
    assert "ContextLifecycleMiddleware" not in names
    assert "ModelExecutionMiddleware" not in names
    assert "ToolExecutionMiddleware" not in names

    stack = build_middleware_inventory(profile="COMMON_QA")
    types = {type(item).__name__ for item in stack}
    assert "LoopDetectionMiddleware" not in types
    assert "ToolCallLimitMiddleware" not in types
    assert "RunGovernorMiddleware" not in types


def test_governor_config_consolidated_under_agent_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """loop_detection / tool_call_limit 段已收敛为 agent_runtime.governor，env 字段同步改名。"""
    from noesis.config.yaml_config import AgentRuntimeYamlSection, AppYamlConfig, GovernorYamlSection

    assert "loop_detection" not in AppYamlConfig.model_fields
    assert "tool_call_limit" not in AgentRuntimeYamlSection.model_fields
    assert "governor" in AgentRuntimeYamlSection.model_fields
    assert hasattr(GovernorYamlSection, "model_fields")
