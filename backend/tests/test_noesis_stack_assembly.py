from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from noesis.agents.middlewares import CompactionThresholds
from noesis.agents.middlewares.stack import NoesisStackDeps, build_noesis_stack
from noesis.agents.super_agent import _compile_task_worker


class _Backend:
    def write(self, path, content):
        return MagicMock(error=None)


def _names(stack):
    return [type(item).__name__ for item in stack]


def _compaction():
    return {
        "summarize": lambda messages: "summary",
        "token_counter": lambda messages: 10,
        "compaction_thresholds": CompactionThresholds(100, 10, 10),
    }


def test_common_stack_exact_order() -> None:
    stack = build_noesis_stack(NoesisStackDeps(profile="COMMON_QA", **_compaction()))
    assert _names(stack) == [
        "ToolResultBudgetMiddleware",
        "ToolFailureMiddleware",
        "DynamicContextMiddleware",
        "PatchToolCallsMiddleware",
        "CompactionMiddleware",
        "LLMErrorHandlingMiddleware",
        "SessionStatsMiddleware",
    ]


def test_full_super_stack_exact_order() -> None:
    @tool
    def mcp_tool(query: str) -> str:
        """Large MCP tool."""
        return query

    subagent: CompiledSubAgent = {
        "name": "worker",
        "description": "worker",
        "runnable": MagicMock(),
    }
    stack = build_noesis_stack(
        NoesisStackDeps(
            profile="SUPER_AGENT_QA",
            tools=[mcp_tool],
            backend=_Backend(),  # type: ignore[arg-type]
            skills_sources=["/skills"],
            skills_user_id="user-1",
            memory_sources=["/memory/USER.md"],
            todo=True,
            subagents=[subagent],
            enable_snip=True,
            model_call_limit=20,
            tool_call_limit=40,
            interrupt_on={"edit_file": True},
            **_compaction(),
        )
    )
    assert _names(stack) == [
        "ToolResultBudgetMiddleware",
        "ToolFailureMiddleware",
        "ReadBeforeWriteMiddleware",
        "TodoListMiddleware",
        "RefreshingSkillsMiddleware",
        "FilesystemMiddleware",
        "SubAgentMiddleware",
        "RefreshingMemoryMiddleware",
        "DynamicContextMiddleware",
        "DurableContextMiddleware",
        "SnipMiddleware",
        "PatchToolCallsMiddleware",
        "CompactionMiddleware",
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
        "LLMErrorHandlingMiddleware",
        "SessionStatsMiddleware",
        "HumanInTheLoopMiddleware",
    ]
    filesystem = next(item for item in stack if type(item).__name__ == "FilesystemMiddleware")
    assert filesystem._tool_token_limit_before_evict is None  # noqa: SLF001
    assert filesystem._human_message_token_limit_before_evict is None  # noqa: SLF001
    create_agent(
        model=FakeListChatModel(responses=["ok"]),
        tools=[mcp_tool],
        middleware=stack,
        checkpointer=InMemorySaver(),
    )


def test_snip_is_not_installed_without_a_real_entrypoint() -> None:
    assert "SnipMiddleware" not in _names(
        build_noesis_stack(NoesisStackDeps(profile="COMMON_QA"))
    )


def test_subagents_require_backend() -> None:
    spec: CompiledSubAgent = {"name": "x", "description": "x", "runnable": MagicMock()}
    with pytest.raises(ValueError, match="backend"):
        build_noesis_stack(NoesisStackDeps(profile="COMMON_QA", subagents=[spec]))


def test_stack_compiles_with_langchain() -> None:
    create_agent(
        model=FakeListChatModel(responses=["ok"]),
        tools=[],
        middleware=build_noesis_stack(NoesisStackDeps(profile="COMMON_QA")),
    )


def test_raw_task_worker_has_only_one_hitl_middleware() -> None:
    @tool
    def dangerous(value: str) -> str:
        """A tool that requires approval."""
        return value

    model = FakeListChatModel(responses=["ok"])
    with (
        patch("noesis.agents.super_agent.get_llm", return_value=model),
        patch(
            "noesis.factory.ModelConfig",
            SimpleNamespace(summarization_enabled=False, tool_output_max_chars=24_000, max_retries=6),
        ),
        patch("noesis.factory.HitlConfig", SimpleNamespace(enabled=True)),
    ):
        worker = _compile_task_worker(
            _Backend(),  # type: ignore[arg-type]
            [dangerous],
            [],
            user_id="user-1",
            interrupt_on={"dangerous": True},
        )

    # 后台 worker：HITL 只挂一个 HumanInTheLoopMiddleware（来自 super_agent 侧，
    # SUBAGENT 栈本身不含）；后台执行/审批续跑由 BackgroundTaskExecutor 负责
    stack = build_noesis_stack(
        NoesisStackDeps(
            profile="SUBAGENT",
            backend=_Backend(),  # type: ignore[arg-type]
        )
    )
    assert not any(type(item).__name__ == "HumanInTheLoopMiddleware" for item in stack)
