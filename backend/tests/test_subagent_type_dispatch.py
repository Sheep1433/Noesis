"""subagent 类型分发契约：角色注册表 + NoesisSubagentMiddleware + descriptor。

覆盖：
- 注册表：重名拒绝、生效模型解析（绑定/沿用父模型）、worker 工具集防线
- descriptor：版本化读取校验（合法/缺键/坏版本/坏结构）
- 中间件：未知类型拒绝且无副作用、Command 写入任务身份、state 快照过期
  不误导（check 永远实时查执行器）、prompt 注入类型清单
- 真实图：create_agent + middleware，start_task 工具调用后 bg_tasks 落
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

from noesis.agents.subagents.executor import (
    BackgroundTaskExecutor,
    BgTaskStatus,
)
from noesis.agents.subagents.registry import (
    SubagentRegistry,
    SubagentRole,
    assert_no_bg_task_tools,
)
from noesis.agents.subagents.tools_middleware import (
    NoesisSubagentMiddleware,
    _merge_bg_tasks,
)
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
                create_child_session=None) -> NoesisSubagentMiddleware:
    return NoesisSubagentMiddleware(
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

    evil = [StructuredTool.from_function(func=lambda: "x", name="start_task", description="递归入口")]
    with pytest.raises(ValueError, match="start_task"):
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
    start = next(t for t in middleware.tools if t.name == "start_task")

    result = await start.ainvoke({
        "description": "调研", "subagent_type": "research", "run_in_background": True,
    })

    assert "未知子 Agent 类型 research" in result
    assert "general" in result  # 可用类型清单
    assert launched == []
    assert executor.list_for_session("s-dispatch") == []


@pytest.mark.asyncio
async def test_background_start_returns_command_with_identity() -> None:
    """后台启动：Command 回 ToolMessage（文本不变）+ bg_tasks 身份写入。"""
    from langgraph.types import Command

    executor = BackgroundTaskExecutor(task_timeout_seconds=30)

    async def create_child_session(description, prompt, tool_call_id="", subagent_type="general", model_id=None):
        return {"child_session_id": "child-1", "run_id": "run-1"}

    middleware = _middleware(executor, _registry(), create_child_session)
    start = next(t for t in middleware.tools if t.name == "start_task")

    result = await start.ainvoke({
        "description": "调研 X", "subagent_type": "general", "run_in_background": True,
    })

    assert isinstance(result, Command)
    (tool_message,) = result.update["messages"]
    assert tool_message.tool_call_id == ""
    assert "子 Agent 已启动：child-1" in tool_message.content
    identity = next(iter(result.update["bg_tasks"].values()))
    assert identity["subagent_type"] == "general"
    assert identity["description"] == "调研 X"
    assert identity["last_status"] == BgTaskStatus.RUNNING.value
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
    check = next(t for t in middleware.tools if t.name == "check_task")
    # 即便 state 快照停留在 running（构造过期快照），check 输出实时终态
    text = await check.ainvoke({"task_id": task_id})
    assert "completed" in text
    assert "实时性" in task["description"]


def test_merge_bg_tasks_keeps_terminal_entries() -> None:
    """reducer 按 task_id 合并；终态条目保留（压缩后已收结果的任务仍可追溯）。"""
    first = {"t1": {"task_id": "t1", "child_session_id": "c1",
                    "subagent_type": "general", "description": "a", "last_status": "completed"}}
    second = {"t2": {"task_id": "t2", "child_session_id": "c2",
                     "subagent_type": "general", "description": "b", "last_status": "running"}}
    merged = _merge_bg_tasks(first, second)
    assert set(merged) == {"t1", "t2"}
    # 同 id 更新覆盖旧值
    updated = _merge_bg_tasks(first, {"t1": {**first["t1"], "last_status": "running"}})
    assert updated["t1"]["last_status"] == "running"


# ---------------------------------------------------------------------------
# 真实图：Command 经 create_agent 落 state，跨轮存活
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_task_command_persists_bg_tasks_across_turns() -> None:
    """主 Agent 图内调用 start_task：bg_tasks 落 checkpoint，下一轮仍在。"""
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)

    async def create_child_session(description, prompt, tool_call_id="", subagent_type="general", model_id=None):
        return {"child_session_id": f"child-{description}", "run_id": f"run-{description}"}

    model = _ScriptedToolModel(script=[
        AIMessage(content="", tool_calls=[{
            "name": "start_task", "id": "call-1", "type": "tool_call",
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

    bg_tasks = result.get("bg_tasks") or {}
    assert any(
        ident["subagent_type"] == "general" and ident["description"] == "委派任务"
        for ident in bg_tasks.values()
    ), f"bg_tasks 未写入 state: {result.keys()}"
    # prompt 注入：模型看到的 system message 含类型清单
    assert "- general: 通用子 Agent" in model._seen_systems[0]

    # 第二轮（同 thread）：checkpoint 内 bg_tasks 存活
    result2 = await agent.ainvoke({"messages": [HumanMessage(content="进度如何")]}, config)
    bg_tasks2 = result2.get("bg_tasks") or {}
    assert set(bg_tasks2) == set(bg_tasks)

    for task_id in list(bg_tasks):
        executor.cancel(task_id)
