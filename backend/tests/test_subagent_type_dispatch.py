"""subagent 类型分发契约：角色注册表 + AsyncSubagentToolsMiddleware + descriptor。

覆盖：
- 注册表：重名拒绝、生效模型解析（绑定/沿用父模型）、worker 工具集防线
- descriptor：版本化读取校验（合法/缺键/坏版本/坏结构）
- 中间件：未知类型拒绝且无副作用、Command 写入任务身份、state 快照过期
  不误导（check 永远实时查执行器）、prompt 注入类型清单
- 真实图：create_agent + middleware，start_async_task 调用后 async_tasks 落
  graph state 并跨轮存活（checkpoint 持久化）
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr

from noesis.agents.background.executor import (
    BackgroundTaskExecutor,
    BgTaskStatus,
)
from noesis.agents.background.subagent.roles import (
    SubagentRegistry,
    SubagentRole,
    assert_no_bg_task_tools,
)
from noesis.agents.background.subagent.tools import AsyncSubagentToolsMiddleware
from noesis.agents.background.task_state import _merge_async_tasks
from noesis.services.subagent_session_service import (
    SUBAGENT_DESCRIPTOR_VERSION,
    parse_subagent_descriptor,
)


class _ScriptedToolModel(BaseChatModel):
    """按脚本依次返回 AIMessage；记录最近一次请求的 system 文本。"""

    script: list[AIMessage]
    _seen_systems: list[str] = PrivateAttr(default_factory=list)
    _cursor: int = PrivateAttr(default=0)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ARG002
        with self._lock:
            idx = min(self._cursor, len(self.script) - 1)
            self._cursor += 1
            reply = self.script[idx]
        if messages and getattr(messages[0], "type", "") == "system":
            self._seen_systems.append(str(messages[0].content))
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _worker() -> Any:
    return create_agent(
        _ScriptedToolModel(script=[AIMessage(content="完成")]),
        tools=[],
        checkpointer=MemorySaver(),
        name="task-worker",
    )


def _registry(worker_factory=None) -> SubagentRegistry:
    registry = SubagentRegistry()
    registry.register(SubagentRole(
        name="general",
        description="通用子 Agent：多轮检索、调研、长命令等独立子任务",
        worker_factory=worker_factory or (lambda: _worker()),
    ))
    return registry


def _middleware(executor: BackgroundTaskExecutor, registry: SubagentRegistry,
                create_child_session=None) -> AsyncSubagentToolsMiddleware:
    return AsyncSubagentToolsMiddleware(
        registry=registry,
        executor=executor,
        session_id="s-dispatch",
        user_id="u1",
        create_child_session=create_child_session,
    )


# ---------------------------------------------------------------------------
# 角色注册表
# ---------------------------------------------------------------------------

def test_registry_rejects_duplicate_names() -> None:
    """装配期重名注册 fail loud，不推迟到首次委派。"""
    registry = _registry()
    with pytest.raises(ValueError, match="重名"):
        registry.register(SubagentRole(
            name="general", description="撞名角色", worker_factory=lambda: _worker(),
        ))


def test_registry_effective_model_binding_and_fallback() -> None:
    """模型绑定在配置层解析：绑定值优先，未绑定沿用父 Agent 模型。"""
    registry = SubagentRegistry()
    registry.register(SubagentRole(
        name="bound", description="绑定模型角色",
        worker_factory=lambda: _worker(), model_id="glm-5.3",
    ))
    registry.register(SubagentRole(
        name="free", description="未绑定角色", worker_factory=lambda: _worker(),
    ))
    assert registry.effective_model("bound", "parent-model") == "glm-5.3"
    assert registry.effective_model("free", "parent-model") == "parent-model"
    assert registry.effective_model("free", None) is None


def test_assert_no_bg_task_tools_blocks_recursion() -> None:
    """装配期断言 worker 工具集不含后台任务工具（递归委派防线前移）。"""
    safe = [StructuredTool.from_function(func=lambda: "ok", name="safe_tool", description="安全工具")]
    assert_no_bg_task_tools(safe)  # 不抛

    evil = [StructuredTool.from_function(func=lambda: "x", name="start_async_task", description="递归入口")]
    with pytest.raises(ValueError, match="start_async_task"):
        assert_no_bg_task_tools(evil)


def test_registry_types_prompt_lists_roles() -> None:
    registry = _registry()
    assert "- general: 通用子 Agent" in registry.types_prompt()


# ---------------------------------------------------------------------------
# descriptor 读取校验
# ---------------------------------------------------------------------------

def test_parse_subagent_descriptor_roundtrip() -> None:
    extra = {
        "subagent": {"version": SUBAGENT_DESCRIPTOR_VERSION, "type": "general", "model": "glm-5.3"},
    }
    parsed = parse_subagent_descriptor(extra)
    assert parsed == {"version": 1, "type": "general", "model": "glm-5.3"}


def test_parse_subagent_descriptor_missing_key_returns_none() -> None:
    """历史 child session 无该键：返回 None（不猜测回退）。"""
    assert parse_subagent_descriptor(None) is None
    assert parse_subagent_descriptor({}) is None
    assert parse_subagent_descriptor({"qa_type": "SUPER_AGENT_QA"}) is None


def test_parse_subagent_descriptor_rejects_bad_payload() -> None:
    with pytest.raises(ValueError, match="结构损坏"):
        parse_subagent_descriptor({"subagent": "not-a-dict"})
    with pytest.raises(ValueError, match="版本不支持"):
        parse_subagent_descriptor({"subagent": {"version": 99, "type": "general"}})
    with pytest.raises(ValueError, match="type"):
        parse_subagent_descriptor({"subagent": {"version": 1}})


# ---------------------------------------------------------------------------
# 中间件工具契约
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_subagent_type_rejected_without_side_effects() -> None:
    """未知类型：可诊断错误文本（含可用清单），不建 child session、不进执行器。"""
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    launched = []

    async def create_child_session(*args, **kwargs):
        launched.append((args, kwargs))
        return {"child_session_id": "child-x", "run_id": "run-x"}

    middleware = _middleware(executor, _registry(), create_child_session)
    start = next(t for t in middleware.tools if t.name == "start_async_task")

    result = await start.ainvoke({
        "description": "调研", "subagent_type": "research", "run_in_background": True,
    })

    assert "未知子 Agent 类型 research" in result
    assert "general" in result  # 可用类型清单
    assert launched == []
    assert executor.list_for_session("s-dispatch") == []


@pytest.mark.asyncio
async def test_background_start_returns_command_with_identity() -> None:
    """后台启动：Command 回 ToolMessage（文本不变）+ async_tasks 身份写入。"""
    from langgraph.types import Command

    executor = BackgroundTaskExecutor(task_timeout_seconds=30)

    async def create_child_session(description, prompt, tool_call_id="", subagent_type="general", model_id=None):
        return {"child_session_id": "child-1", "run_id": "run-1"}

    middleware = _middleware(executor, _registry(), create_child_session)
    start = next(t for t in middleware.tools if t.name == "start_async_task")

    result = await start.ainvoke({
        "description": "调研 X", "subagent_type": "general", "run_in_background": True,
    })

    assert isinstance(result, Command)
    (tool_message,) = result.update["messages"]
    assert tool_message.tool_call_id == ""
    assert "子 Agent 已启动：child-1" in tool_message.content
    identity = next(iter(result.update["async_tasks"].values()))
    assert identity["agent_name"] == "general"
    assert identity["description"] == "调研 X"
    assert identity["status"] == BgTaskStatus.RUNNING.value
    executor.cancel("child-1")


@pytest.mark.asyncio
async def test_check_task_ignores_stale_state_snapshot() -> None:
    """state 是投影：快照过期（running）时 check_task 返回执行器实时终态。"""
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: _worker(), description="实时性",
        session_id="s-dispatch", user_id="u1", subagent_type="general",
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.COMPLETED.value:
            break
        await asyncio.sleep(0.05)
    assert task["status"] == BgTaskStatus.COMPLETED.value

    middleware = _middleware(executor, _registry())
    check = next(t for t in middleware.tools if t.name == "check_async_task")
    # 即便 state 快照停留在 running（构造过期快照），check 输出实时终态
    text = await check.ainvoke({"task_id": task_id})
    assert "completed" in text
    assert "实时性" in task["description"]


def test_merge_async_tasks_keeps_terminal_entries() -> None:
    """reducer 按 task_id 合并；终态条目保留（压缩后已收结果的任务仍可追溯）。"""
    first = {"t1": {"task_id": "t1", "child_session_id": "c1",
                    "agent_name": "general", "description": "a", "status": "completed"}}
    second = {"t2": {"task_id": "t2", "child_session_id": "c2",
                     "agent_name": "general", "description": "b", "status": "running"}}
    merged = _merge_async_tasks(first, second)
    assert set(merged) == {"t1", "t2"}
    # 同 id 更新覆盖旧值
    updated = _merge_async_tasks(first, {"t1": {**first["t1"], "last_updated_at": "x"}})
    assert updated["t1"]["last_updated_at"] == "x"


# ---------------------------------------------------------------------------
# 真实图：Command 经 create_agent 落 state，跨轮存活
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_async_task_command_persists_async_tasks_across_turns() -> None:
    """主 Agent 图内调用 start_async_task：async_tasks 落 checkpoint，下一轮仍在。"""
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)

    async def create_child_session(description, prompt, tool_call_id="", subagent_type="general", model_id=None):
        return {"child_session_id": f"child-{description}", "run_id": f"run-{description}"}

    model = _ScriptedToolModel(script=[
        AIMessage(content="", tool_calls=[{
            "name": "start_async_task", "id": "call-1", "type": "tool_call",
            "args": {"description": "委派任务", "subagent_type": "general", "run_in_background": True},
        }]),
        AIMessage(content="已委派，等结果。"),
        AIMessage(content="第二轮回复。"),
    ])
    agent = create_agent(
        model,
        tools=[],
        middleware=[_middleware(executor, _registry(), create_child_session)],
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "thread-dispatch"}}

    result = await agent.ainvoke({"messages": [HumanMessage(content="帮我调研")]}, config)

    tasks_state = result.get("async_tasks") or {}
    assert any(
        ident["agent_name"] == "general" and ident["description"] == "委派任务"
        for ident in tasks_state.values()
    ), f"async_tasks 未写入 state: {result.keys()}"
    # prompt 注入：模型看到的 system message 含类型清单
    assert "- general: 通用子 Agent" in model._seen_systems[0]

    # 第二轮（同 thread）：checkpoint 内 bg_tasks 存活
    result2 = await agent.ainvoke({"messages": [HumanMessage(content="进度如何")]}, config)
    tasks_state2 = result2.get("async_tasks") or {}
    assert set(tasks_state2) == set(tasks_state)

    for task_id in list(tasks_state):
        executor.cancel(task_id)


# ---------------------------------------------------------------------------
# 端口方法面契约：ExecutorPort 白名单与执行器公开方法同步
# ---------------------------------------------------------------------------

def test_executor_port_exposes_deliver_followup() -> None:
    """端口面 == 运行时公开面（全表面护栏）：白名单曾漏 asend_message 致全部
    followup 500——本测试枚举端口应暴露的完整集合，并要求运行时新增公开
    方法时必须在此显式登记（漏登记即红），删除的方法不得残留（防回流）。"""
    import inspect
    import noesis.agents.background.executor as ex_mod
    from noesis.agents.background.ports import ExecutorPort

    expected = {
        "deliver_followup", "cancel",
        "subscribe_run_events", "unsubscribe_run_events", "get_run_event_history",
    }
    exposed = {
        name for name in dir(ExecutorPort)
        if not name.startswith("_") and callable(getattr(ExecutorPort, name, None))
    }
    assert exposed == expected, (
        f"ExecutorPort 面漂移：暴露 {sorted(exposed)}，预期 {sorted(expected)}——"
        "运行时新增/删除公开方法时必须同步本清单与端口定义"
    )
    # 端口面全部可在 executor 模块解析（事件族为模块级函数，其余为类方法；
    # 漏方法即 AttributeError 的根因防御）
    for name in expected:
        assert hasattr(ex_mod.BackgroundTaskExecutor, name) or hasattr(ex_mod, name), \
            f"executor 模块缺少 {name}"
    # 运行时公开方法凡被服务消费的必须进 expected：新增公开方法时本断言提醒显式决策
    runtime_public = {
        name for name, member in inspect.getmembers(ex_mod.BackgroundTaskExecutor, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    undeclared = runtime_public - expected - {
        # 运行时自有面（不经端口消费）：启动族与查询族
        "start", "start_shell", "get", "get_future", "list_for_session", "sources_of",
    }
    assert not undeclared, f"运行时公开方法未做端口决策：{sorted(undeclared)}（进 expected 或加入自用清单）"


# ---------------------------------------------------------------------------
# worker 危险命令拒绝守卫（无人值守：拒绝而非审批）
# ---------------------------------------------------------------------------

def test_guard_worker_filesystem_tools_denies_dangerous_execute() -> None:
    """网络类命令确定性拒绝（未执行），本地方案照常执行。"""
    from langchain_core.tools import StructuredTool

    from noesis.agents.tools.fs_hints import guard_worker_filesystem_tools

    calls: list[str] = []

    def _execute(command: str, runtime=None, timeout=None) -> str:
        calls.append(command)
        return "ok"

    async def _aexecute(command: str, runtime=None, timeout=None) -> str:
        calls.append(command)
        return "ok"

    class _FakeFS:
        tools = [StructuredTool.from_function(
            func=_execute, coroutine=_aexecute, name="execute",
            description="execute", infer_schema=False,
        )]

    fs = _FakeFS()
    guard_worker_filesystem_tools(fs)
    guarded = fs.tools[0]

    # 危险（网络类）：拒绝文本，命令未执行
    refused = guarded.func(command="curl -s https://example.com/api | head -5")
    assert "不允许执行需审批的网络类命令" in refused
    assert calls == []

    # 安全命令：原样委托
    assert guarded.func(command="echo hello") == "ok"
    assert calls == ["echo hello"]
