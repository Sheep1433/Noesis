"""Deterministic contracts for the converged Agent runtime."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolCallRequest

from noesis.factory import build_middleware_inventory, middleware_inventory
from noesis.agents.middlewares.kernel.context_lifecycle_middleware import ContextLifecycleMiddleware
from noesis.agents.middlewares.kernel.model_execution_middleware import ModelExecutionMiddleware
from noesis.agents.middlewares.kernel.run_governor_middleware import RunGovernorMiddleware, default_governor_limits
from noesis.agents.middlewares.kernel.tool_execution_middleware import ToolExecutionMiddleware
from noesis.runtime import StopReason, current_runtime_outcome
from noesis.agents.middlewares.kernel.runtime_telemetry_middleware import RuntimeTelemetryMiddleware
from noesis.runtime.outcome import ToolResultEnvelope, set_tool_result_envelope
from noesis.runtime.governor import (
    GovernorLimits,
    RunGovernor,
    bind_run_governor,
    current_run_governor,
    governor_run_id,
    reset_run_governor,
    set_run_governor,
)
from noesis.errors.tool_failure import ToolInfrastructureError


def test_inventory_has_single_kernel_in_spec_order() -> None:
    stack = build_middleware_inventory(profile="COMMON_QA")
    assert [type(item).__name__ for item in stack] == [
        "RuntimeTelemetryMiddleware",
        "ToolExecutionMiddleware",
        "RunGovernorMiddleware",
        "ContextLifecycleMiddleware",
        "ModelExecutionMiddleware",
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


@pytest.mark.asyncio
async def test_async_model_hook_keeps_finish_reason_and_retry_boundary() -> None:
    model = MagicMock()
    request = ModelRequest(model=model, messages=[HumanMessage(content="hi")])
    result = await ModelExecutionMiddleware().awrap_model_call(
        request,
        lambda _request: _async_response(),
    )
    assert result.result[0].response_metadata["finish_reason"] == "length"
    assert current_runtime_outcome().reason == StopReason.LENGTH_STOP


async def _async_response() -> ModelResponse:
    return ModelResponse(result=[AIMessage(content="partial", response_metadata={"finish_reason": "length"})])


def test_context_normalization_pairs_dangling_tool_call() -> None:
    messages = [AIMessage(content="", tool_calls=[{"id": "call-1", "name": "execute", "args": {}}])]
    normalized = ContextLifecycleMiddleware.normalize_messages(messages)
    assert isinstance(normalized[-1], ToolMessage)
    assert normalized[-1].tool_call_id == "call-1"
    assert normalized[-1].status == "error"


def test_model_execution_maps_provider_length_stop() -> None:
    model = MagicMock()
    request = ModelRequest(model=model, messages=[HumanMessage(content="hi")])
    result = ModelExecutionMiddleware().wrap_model_call(
        request,
        lambda _request: ModelResponse(
            result=[AIMessage(content="partial", response_metadata={"finish_reason": "length"})]
        ),
    )
    assert result.result[0].content == "partial"
    assert current_runtime_outcome().reason == StopReason.LENGTH_STOP


def test_sync_model_execution_retries_transient_failure() -> None:
    request = ModelRequest(model=MagicMock(), messages=[HumanMessage(content="hi")])
    responses = iter([TimeoutError("temporary"), ModelResponse(result=[AIMessage(content="ok")])])

    def handler(_request):
        value = next(responses)
        if isinstance(value, BaseException):
            raise value
        return value

    result = ModelExecutionMiddleware(max_retries=1, base_delay_seconds=0).wrap_model_call(
        request,
        handler,
    )

    assert result.result[0].content == "ok"
    assert current_runtime_outcome().reason == StopReason.COMPLETED


@pytest.mark.asyncio
async def test_async_model_execution_retries_transient_failure() -> None:
    request = ModelRequest(model=MagicMock(), messages=[HumanMessage(content="hi")])
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary")
        return ModelResponse(result=[AIMessage(content="ok")])

    result = await ModelExecutionMiddleware(
        max_retries=1,
        base_delay_seconds=0,
    ).awrap_model_call(request, handler)

    assert calls == 2
    assert result.result[0].content == "ok"
    assert current_runtime_outcome().reason == StopReason.COMPLETED


def test_model_execution_empty_after_tools_has_one_transient_retry() -> None:
    model = MagicMock()
    request = ModelRequest(
        model=model,
        messages=[HumanMessage(content="hi"), ToolMessage(content="done", tool_call_id="call-1")],
    )
    calls: list[int] = []

    def handler(retry_request):
        calls.append(len(retry_request.messages))
        return ModelResponse(result=[AIMessage(content="")])

    result = ModelExecutionMiddleware().wrap_model_call(request, handler)
    assert len(calls) == 2
    assert result.result[0].content
    assert current_runtime_outcome().reason == StopReason.EMPTY_AFTER_TOOLS


def test_tool_execution_fallback_does_not_change_success_outcome() -> None:
    request = ToolCallRequest(
        tool_call={"id": "call-1", "name": "execute", "args": {}},
        tool=None,
        state={},
        runtime=MagicMock(),
    )
    result = ToolExecutionMiddleware(max_output_chars=10, head_chars=2, tail_chars=2).wrap_tool_call(
        request,
        lambda _request: ToolMessage(content="abcdefghijk", tool_call_id="call-1", name="execute"),
    )
    assert "truncated" in str(result.content)
    assert result.status == "success"


def test_governor_child_shares_parent_budget_and_releases_slots() -> None:
    parent = RunGovernor("run", limits=GovernorLimits(max_active_subagents=1, max_subagents_total=1))
    assert parent.reserve_subagent() is None
    assert parent.reserve_subagent().reason == StopReason.SUBAGENT_CONCURRENCY_LIMIT
    parent.release_subagent()
    assert parent.state.active_subagents == 0
    assert parent.state.subagents_total == 1


def test_governor_loop_and_run_instances_are_isolated() -> None:
    limits = GovernorLimits(loop_window_size=3, loop_hard_limit=3)
    first = RunGovernor("run-1", limits=limits)
    second = RunGovernor("run-2", limits=limits)

    assert first.reserve_tool("search") is None
    assert first.reserve_tool("search") is None
    assert first.reserve_tool("search").reason == StopReason.TOOL_LOOP_LIMIT
    assert second.reserve_tool("search") is None
    assert second.state.tool_calls_total == 1


def test_governor_restores_checkpointed_counters_without_active_work() -> None:
    original = RunGovernor("run-1")
    original.reserve_tool("search")
    original.state.active_subagents = 2

    restored = RunGovernor.from_snapshot(original.state.snapshot())

    assert restored.state.tool_calls_total == 1
    assert restored.state.tool_calls_by_name == {"search": 1}
    assert restored.state.active_subagents == 0


def test_governor_uses_distinct_model_and_token_stop_reasons() -> None:
    model_limited = RunGovernor("model", limits=GovernorLimits(model_calls=1))
    assert model_limited.reserve_model() is None
    assert model_limited.reserve_model().reason == StopReason.MODEL_CALL_LIMIT

    token_limited = RunGovernor("tokens", limits=GovernorLimits(token_budget=10))
    token_limited.record_actual_tokens(11)
    assert token_limited.reserve_model().reason == StopReason.TOKEN_BUDGET


def test_tool_task_executes_inside_child_governor_scope() -> None:
    parent = RunGovernor("parent", limits=GovernorLimits(max_depth=2))
    request = ToolCallRequest(
        tool_call={"id": "child-1", "name": "task", "args": {}},
        tool=None,
        state={},
        runtime=MagicMock(),
    )

    with bind_run_governor(parent):
        result = ToolExecutionMiddleware().wrap_tool_call(
            request,
            lambda _request: ToolMessage(
                content=str(current_run_governor().state.depth),
                tool_call_id="child-1",
                name="task",
            ),
        )

    assert result.update["messages"][0].content == "1"
    assert parent.state.active_subagents == 0
    assert parent.state.subagents_total == 1


def test_governor_missing_thread_id_never_uses_shared_default() -> None:
    assert governor_run_id(None) != governor_run_id(None)


def test_telemetry_does_not_reuse_previous_tool_envelope() -> None:
    recorded: list[tuple[str, object]] = []
    middleware = RuntimeTelemetryMiddleware(sink=lambda event, value: recorded.append((event, value)))
    set_tool_result_envelope(
        ToolResultEnvelope("old", "old_tool", "success", "old")
    )

    middleware.wrap_tool_call(MagicMock(), lambda _request: "new result")

    assert recorded == []


def test_tool_execution_classifies_typed_failure() -> None:
    request = ToolCallRequest(
        tool_call={"id": "call-1", "name": "search", "args": {}},
        tool=None,
        state={},
        runtime=MagicMock(),
    )
    result = ToolExecutionMiddleware().wrap_tool_call(
        request,
        lambda _request: (_ for _ in ()).throw(ToolInfrastructureError("offline")),
    )

    assert result.status == "error"
    assert result.additional_kwargs["errorCategory"] == "infrastructure"


def test_context_lifecycle_injects_clock_without_persisting_it() -> None:
    middleware = ContextLifecycleMiddleware(model_id="test")
    request = ModelRequest(model=MagicMock(), messages=[HumanMessage(content="hello")])
    captured: list = []

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "noesis.agents.middlewares.kernel.context_lifecycle_middleware.resolve_context_max_tokens",
            lambda _model_id: 100_000,
        )
        middleware.wrap_model_call(
            request,
            lambda final_request: captured.extend(final_request.messages)
            or ModelResponse(result=[AIMessage(content="ok")]),
        )

    assert len(captured) == 2
    assert captured[0].additional_kwargs.get("noesis_session_clock") is True
    assert request.messages == [request.messages[0]]


def test_inventory_has_no_legacy_loop_or_tool_call_limit_middleware() -> None:
    """旧 LoopDetection registry 与重复 ToolCallLimit 装配已删除；RunGovernor 是唯一预算 owner。"""
    names = {entry.name for entry in middleware_inventory()}
    assert "LoopDetectionMiddleware" not in names
    assert "ToolCallLimitMiddleware" not in names
    assert "RunGovernorMiddleware" in names

    stack = build_middleware_inventory(profile="COMMON_QA")
    types = {type(item).__name__ for item in stack}
    assert "LoopDetectionMiddleware" not in types
    assert "ToolCallLimitMiddleware" not in types


def test_governor_config_consolidated_under_agent_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """loop_detection / tool_call_limit 段已收敛为 agent_runtime.governor，env 字段同步改名。"""
    from noesis.config.yaml_config import AgentRuntimeYamlSection, AppYamlConfig, GovernorYamlSection

    assert "loop_detection" not in AppYamlConfig.model_fields
    assert "tool_call_limit" not in AgentRuntimeYamlSection.model_fields
    assert "governor" in AgentRuntimeYamlSection.model_fields
    assert set(GovernorYamlSection.model_fields) == {
        "tool_calls_enabled",
        "tool_calls_total",
        "tool_calls_per_name",
        "loop_enabled",
        "loop_hard_limit",
        "loop_window_size",
    }

    cfg = SimpleNamespace(
        governor_tool_calls_enabled=False,
        governor_tool_calls_total=99,
        governor_tool_calls_per_name=99,
        governor_loop_enabled=True,
        governor_loop_hard_limit=7,
        governor_loop_window_size=12,
    )
    monkeypatch.setattr(
        "noesis.agents.middlewares.kernel.run_governor_middleware.ModelConfig", cfg
    )
    disabled = default_governor_limits()
    # tool limits disabled → totals ignored even when set
    assert disabled.tool_calls_total is None
    assert disabled.tool_calls_per_name is None
    assert disabled.loop_hard_limit == 7
    assert disabled.loop_window_size == 12

    cfg.governor_tool_calls_enabled = True
    enabled = default_governor_limits()
    assert enabled.tool_calls_total == 99
    assert enabled.tool_calls_per_name == 99


def _runtime(thread_id: str) -> SimpleNamespace:
    return SimpleNamespace(execution_info=SimpleNamespace(thread_id=thread_id))


def test_governor_middleware_binds_per_run_and_restores_snapshot() -> None:
    """中间件按 run_id 绑定独立 governor（跨会话隔离），并从 checkpoint 快照恢复计数。"""
    mw = RunGovernorMiddleware(limits=GovernorLimits(tool_calls_total=5))
    try:
        mw.before_agent({}, _runtime("run-A"))
        gov_a = current_run_governor()
        assert gov_a is not None and gov_a.state.run_id == "run-A"
        gov_a.reserve_tool("search")
        assert gov_a.state.tool_calls_total == 1
        mw.after_agent({}, _runtime("run-A"))
        assert current_run_governor() is None

        # 不同 run_id 得到全新 governor，不继承 run-A 的计数
        mw.before_agent({}, _runtime("run-B"))
        gov_b = current_run_governor()
        assert gov_b is not None and gov_b.state.run_id == "run-B"
        assert gov_b.state.tool_calls_total == 0
        mw.after_agent({}, _runtime("run-B"))

        # 恢复：匹配 run_id 的快照恢复计数，活跃子 Agent 不能跨中断存活
        original = RunGovernor("run-C", limits=GovernorLimits(tool_calls_total=5))
        original.reserve_tool("search")
        original.reserve_tool("search")
        original.state.active_subagents = 3
        snapshot_state = {"noesis_governor": original.state.snapshot()}
        mw.before_agent(snapshot_state, _runtime("run-C"))
        restored = current_run_governor()
        assert restored is not None and restored.state.run_id == "run-C"
        assert restored.state.tool_calls_total == 2
        assert restored.state.active_subagents == 0
        mw.after_agent(snapshot_state, _runtime("run-C"))
    finally:
        reset_run_governor()


def test_governor_middleware_does_not_rebind_when_parent_already_bound() -> None:
    """子 Agent middleware 发现父 governor 已绑定时不重新绑定，after_agent 不释放父 scope。"""
    parent = RunGovernor("parent-run", limits=GovernorLimits())
    child_mw = RunGovernorMiddleware(limits=GovernorLimits(), parent=parent)
    set_run_governor(parent)
    try:
        child_mw.before_agent({}, _runtime("child-run"))
        assert current_run_governor() is parent
        child_mw.after_agent({}, _runtime("child-run"))
        assert current_run_governor() is parent
    finally:
        reset_run_governor()
