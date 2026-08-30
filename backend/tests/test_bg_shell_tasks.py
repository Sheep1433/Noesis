"""后台命令任务（execute run_in_background）契约。

覆盖 spec「后台命令任务」Requirement：
- start_shell：不经 worker 编译，backend 执行，completed 带 exit code + 输出尾部
- shell 任务不可对话：send_message 拒绝
- 会话沙箱销毁：运行中任务转 failed（容器回收连坐）
- execute 工具替换：同名 + run_in_background 参数；false 原样委托原工具；
  true 立即返回 task_id；超并发优雅拒绝
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.protocol import ExecuteResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, PrivateAttr

from noesis.agents.subagents.executor import (
    BackgroundSubagentExecutor,
    BgTaskStatus,
    fail_session_shell_tasks,
    shutdown as bg_shutdown,
)
from noesis.agents.subagents.shell_tool import replace_execute_tool


class _FakeShellBackend:
    """同步/异步 execute 均可的假沙箱 backend。"""

    def __init__(self, *, delay: float = 0.0, response: ExecuteResponse | None = None) -> None:
        self.delay = delay
        self.response = response or ExecuteResponse(output="hello\nworld", exit_code=0)
        self.executed: list[str] = []

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        await asyncio.sleep(self.delay)
        self.executed.append(command)
        return self.response

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.executed.append(command)
        return self.response


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    bg_shutdown()


def _wait_terminal(executor: BackgroundSubagentExecutor, task_id: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = executor.get(task_id)
        assert task is not None
        if task["status"] == BgTaskStatus.AWAITING_APPROVAL.value or task["status"] in {
            BgTaskStatus.COMPLETED.value,
            BgTaskStatus.FAILED.value,
            BgTaskStatus.CANCELLED.value,
            BgTaskStatus.TIMED_OUT.value,
        }:
            return task
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} 未在 {timeout}s 内到达终态")


def test_start_shell_completes_with_exit_code_and_output_tail() -> None:
    """shell 任务完成：result 带 exit code + 输出；kind=shell；无 worker。"""
    backend = _FakeShellBackend(response=ExecuteResponse(output="line1\nline2", exit_code=3))
    executor = BackgroundSubagentExecutor()
    task_id = executor.start_shell(
        command="make build", backend=backend, session_id="s-sh", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert task["kind"] == "shell"
    assert "exit code: 3" in task["result"]
    assert "line2" in task["result"]
    assert backend.executed == ["make build"]


def test_start_shell_truncates_long_output_tail() -> None:
    """输出超长：仅保留尾部有界字符。"""
    output = "x" * 10_000
    backend = _FakeShellBackend(response=ExecuteResponse(output=output, exit_code=0))
    executor = BackgroundSubagentExecutor()
    task_id = executor.start_shell(
        command="cat big", backend=backend, session_id="s-sh", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert len(task["result"]) < 6_000
    assert task["result"].endswith("x" * 100)
    assert "仅保留尾部" in task["result"]


def test_shell_task_rejects_followup() -> None:
    """shell 命令不可追加对话（不是对话型任务）。"""
    backend = _FakeShellBackend()
    executor = BackgroundSubagentExecutor()
    task_id = executor.start_shell(
        command="echo hi", backend=backend, session_id="s-sh", user_id="u1",
    )
    _wait_terminal(executor, task_id)
    with pytest.raises(ValueError, match="后台命令任务"):
        BackgroundSubagentExecutor.send_message(task_id, "再跑一次")


def test_fail_session_shell_tasks_on_sandbox_destroy() -> None:
    """沙箱销毁：运行中 shell 任务转 failed（容器回收连坐）。"""
    backend = _FakeShellBackend(delay=30.0)
    executor = BackgroundSubagentExecutor()
    task_id = executor.start_shell(
        command="sleep 30", backend=backend, session_id="s-destroy", user_id="u1",
    )
    time.sleep(0.3)
    assert executor.get(task_id)["status"] == BgTaskStatus.RUNNING.value
    fail_session_shell_tasks("s-destroy", reason="会话沙箱已销毁，任务随容器回收终止")
    task = executor.get(task_id)
    assert task["status"] == BgTaskStatus.FAILED.value
    assert "沙箱已销毁" in task["error"]
    # 其他会话不受影响
    task_id2 = executor.start_shell(
        command="echo ok", backend=_FakeShellBackend(), session_id="s-other", user_id="u1",
    )
    task2 = _wait_terminal(executor, task_id2)
    assert task2["status"] == BgTaskStatus.COMPLETED.value


def test_shell_timeout_watchdog_when_configured() -> None:
    """shell_task_timeout_seconds > 0 时 watchdog 生效（默认 0 不限时）。"""
    backend = _FakeShellBackend(delay=30.0)
    executor = BackgroundSubagentExecutor(shell_task_timeout_seconds=0.5)
    task_id = executor.start_shell(
        command="sleep 30", backend=backend, session_id="s-to", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.TIMED_OUT.value


# ---------------------------------------------------------------------------
# execute 工具替换
# ---------------------------------------------------------------------------


class _OriginalSchema(BaseModel):
    command: str
    timeout: int | None = None


def _build_fake_filesystem_middleware() -> tuple[Any, StructuredTool]:
    """构造带 execute 工具的假 FilesystemMiddleware（原工具直通 backend）。"""
    backend = _FakeShellBackend(response=ExecuteResponse(output="fg-output", exit_code=0))

    async def aexecute(command: str, runtime: Any, timeout: int | None = None) -> ToolMessage:
        resp = await backend.aexecute(command, timeout=timeout)
        return ToolMessage(
            content=f"{resp.output}\n[Command succeeded with exit code {resp.exit_code}]",
            name="execute",
            tool_call_id=runtime.tool_call_id,
            status="success",
        )

    def execute(command: str, runtime: Any, timeout: int | None = None) -> ToolMessage:
        resp = backend.execute(command, timeout=timeout)
        return ToolMessage(
            content=f"{resp.output}\n[Command succeeded with exit code {resp.exit_code}]",
            name="execute",
            tool_call_id=runtime.tool_call_id,
            status="success",
        )

    tool = StructuredTool.from_function(
        name="execute", description="original", func=execute, coroutine=aexecute,
        infer_schema=False, args_schema=_OriginalSchema,
    )
    middleware = SimpleNamespace(tools=[tool])
    return middleware, backend


async def _call(tool: StructuredTool, **kwargs: Any) -> Any:
    runtime = SimpleNamespace(tool_call_id="call_test")
    return await tool.coroutine(runtime=runtime, **kwargs)


@pytest.mark.asyncio
async def test_replace_execute_tool_foreground_delegates_unchanged() -> None:
    """run_in_background 缺省/false：原样委托原工具，输出与替换前一致。"""
    middleware, _ = _build_fake_filesystem_middleware()
    executor = BackgroundSubagentExecutor()
    replace_execute_tool(
        middleware, executor=executor, backend=_FakeShellBackend(), session_id="s", user_id="u1",
    )
    replaced = middleware.tools[0]
    assert replaced.name == "execute"
    # schema 带 run_in_background 且默认 False
    schema = replaced.args_schema.model_json_schema()
    assert "run_in_background" in schema["properties"]
    assert schema["properties"]["run_in_background"]["default"] is False
    # 前台路径输出不变
    result = await _call(replaced, command="ls")
    assert "fg-output" in result.content
    assert "[Command succeeded with exit code 0]" in result.content


@pytest.mark.asyncio
async def test_replace_execute_tool_background_starts_shell_task() -> None:
    """run_in_background=true：立即返回 task_id，命令进 shell 任务管线。"""
    middleware, _ = _build_fake_filesystem_middleware()
    shell_backend = _FakeShellBackend()
    executor = BackgroundSubagentExecutor()
    replace_execute_tool(
        middleware, executor=executor, backend=shell_backend, session_id="s-bg", user_id="u1",
    )
    replaced = middleware.tools[0]
    result = await _call(replaced, command="make build", run_in_background=True)
    content = result.content if hasattr(result, "content") else str(result)
    task_id = content.split("：")[1].split("\n")[0]
    assert task_id.startswith("bg-")
    task = _wait_terminal(executor, task_id)
    assert task["kind"] == "shell"
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert shell_backend.executed == ["make build"]


@pytest.mark.asyncio
async def test_replace_execute_tool_queues_when_concurrency_full() -> None:
    """超并发：shell 任务排队（与子 Agent 同语义），启动即返回 task_id。"""
    slow = _FakeShellBackend(delay=30.0)
    executor = BackgroundSubagentExecutor(max_concurrent_per_session=1)
    middleware, _ = _build_fake_filesystem_middleware()
    replace_execute_tool(
        middleware, executor=executor, backend=slow, session_id="s-cap", user_id="u1",
    )
    replaced = middleware.tools[0]
    first = await _call(replaced, command="sleep 30", run_in_background=True)
    first_content = first.content if hasattr(first, "content") else str(first)
    assert "bg-" in first_content
    second = await _call(replaced, command="echo x", run_in_background=True)
    second_content = second.content if hasattr(second, "content") else str(second)
    assert "后台命令任务已启动" in second_content
    assert "bg-" in second_content
    executor.cancel(first_content.split("：")[1].split("\n")[0])
    executor.cancel(second_content.split("：")[1].split("\n")[0])


@pytest.mark.asyncio
async def test_replace_execute_tool_noop_without_execute_tool() -> None:
    """无 execute 工具（backend 不支持执行）：静默跳过。"""
    middleware = SimpleNamespace(tools=[])
    replace_execute_tool(
        middleware, executor=BackgroundSubagentExecutor(),
        backend=_FakeShellBackend(), session_id="s", user_id="u1",
    )
    assert middleware.tools == []


def test_shell_cancel_notifies_exactly_once() -> None:
    """取消：终态通知只发一次（cancel 方发布，协程 CancelledError 不重复）。"""
    from noesis.agents.subagents import notifications

    notifications._PENDING.pop("s-cn", None)
    backend = _FakeShellBackend(delay=30.0)
    executor = BackgroundSubagentExecutor()
    task_id = executor.start_shell(
        command="sleep 30", backend=backend, session_id="s-cn", user_id="u1",
    )
    import time as _t
    _t.sleep(0.3)
    executor.cancel(task_id)
    _t.sleep(0.3)
    notices = notifications.take_undelivered("s-cn", mark_delivered=False)
    cancelled = [n for n in notices if n.get("task_id") == task_id]
    assert len(cancelled) == 1, f"expected exactly 1 notice, got {len(cancelled)}"
    assert cancelled[0]["status"] == "cancelled"


def test_shell_timeout_passthrough_to_backend() -> None:
    """模型显式 timeout 透传 backend；缺省用默认命令超时（3600）。"""
    received: list[int | None] = []

    class _RecordingBackend(_FakeShellBackend):
        async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
            received.append(timeout)
            return await super().aexecute(command, timeout=timeout)

    executor = BackgroundSubagentExecutor()
    tid1 = executor.start_shell(
        command="a", backend=_RecordingBackend(), session_id="s-to2", user_id="u1", timeout=600,
    )
    tid2 = executor.start_shell(
        command="b", backend=_RecordingBackend(), session_id="s-to2", user_id="u1",
    )
    _wait_terminal(executor, tid1)
    _wait_terminal(executor, tid2)
    assert received == [600, 3600]


@pytest.mark.asyncio
async def test_replace_execute_tool_background_validates_timeout() -> None:
    """后台路径复用前台 timeout 校验（负数/超上限拒绝，不触达 backend）。"""
    middleware, _ = _build_fake_filesystem_middleware()
    executor = BackgroundSubagentExecutor()
    replace_execute_tool(
        middleware, executor=executor, backend=_FakeShellBackend(delay=30.0),
        session_id="s-v", user_id="u1",
    )
    replaced = middleware.tools[0]
    bad = await _call(replaced, command="x", timeout=-1, run_in_background=True)
    bad_content = bad.content if hasattr(bad, "content") else str(bad)
    assert "启动失败" in bad_content and "non-negative" in bad_content
    bad2 = await _call(replaced, command="x", timeout=99999, run_in_background=True)
    bad2_content = bad2.content if hasattr(bad2, "content") else str(bad2)
    assert "exceeds maximum" in bad2_content


# ---------------------------------------------------------------------------
# 全栈集成：真实 middleware 栈（FilesystemMiddleware + 可选 HITL）× 替换后的 execute
# 单元测试全部用假 middleware；这里验证替换工具在真实装配下与
# 工具运行时注入、agent 循环、HITL 审批的实际交互。
# ---------------------------------------------------------------------------


class _ScriptedModel(BaseChatModel):
    """按脚本返回 AIMessage（可带 execute 工具调用）；脚本耗尽返回收尾文本。"""

    script: list
    _cursor: int = PrivateAttr(default=0)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "scripted-execute-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        with self._lock:
            idx = self._cursor
            self._cursor += 1
        message = self.script[idx] if idx < len(self.script) else AIMessage(content="完成")
        return ChatResult(generations=[ChatGeneration(message=message)])


def _execute_call(command: str, call_id: str = "call_exec", **extra: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "execute", "args": {"command": command, **extra}, "id": call_id, "type": "tool_call"}],
    )


def _build_full_agent(tmp_path, *, executor, script, interrupt_on=None):
    """真实装配：LocalShellBackend + FilesystemMiddleware（execute 替换）+ 可选 HITL。"""
    from deepagents.backends.local_shell import LocalShellBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents import create_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    backend = LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True, timeout=5)
    fm = FilesystemMiddleware(backend=backend)
    replace_execute_tool(fm, executor=executor, backend=backend, session_id="s-full", user_id="u1")
    middleware = [fm]
    if interrupt_on:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
    return create_agent(
        _ScriptedModel(script=list(script)),
        tools=[],
        middleware=middleware,
        checkpointer=MemorySaver(),
    )


@pytest.mark.asyncio
async def test_fullstack_foreground_execute_unchanged(tmp_path) -> None:
    """真实栈前台：输出格式与 deepagents 原生一致；不产生后台任务。"""
    executor = BackgroundSubagentExecutor()
    agent = _build_full_agent(
        tmp_path, executor=executor, script=[_execute_call("echo fg-ok")],
    )
    config = {"configurable": {"thread_id": "t-fg"}}
    final_state = None
    async for chunk in agent.astream(
        {"messages": [HumanMessage(content="跑命令")]}, config, stream_mode="values",
    ):
        final_state = chunk
    tool_msgs = [m for m in final_state["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs, "应产生 execute 的 ToolMessage"
    assert "fg-ok" in tool_msgs[-1].content
    assert "[Command succeeded with exit code 0]" in tool_msgs[-1].content
    assert BackgroundSubagentExecutor.list_for_session("s-full") == []


@pytest.mark.asyncio
async def test_fullstack_background_execute_string_return(tmp_path) -> None:
    """真实栈后台：字符串返回值经 agent 循环转为 ToolMessage，任务入注册表。"""
    executor = BackgroundSubagentExecutor()
    agent = _build_full_agent(
        tmp_path, executor=executor,
        script=[_execute_call("echo bg-ok", run_in_background=True)],
    )
    config = {"configurable": {"thread_id": "t-bg"}}
    final_state = None
    async for chunk in agent.astream(
        {"messages": [HumanMessage(content="跑长命令")]}, config, stream_mode="values",
    ):
        final_state = chunk
    tool_msgs = [m for m in final_state["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs, "后台分支的字符串返回应被 agent 转为 ToolMessage"
    assert "后台命令任务已启动" in tool_msgs[-1].content
    tasks = BackgroundSubagentExecutor.list_for_session("s-full")
    assert len(tasks) == 1 and tasks[0]["kind"] == "shell"
    task = _wait_terminal(executor, tasks[0]["task_id"])
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert "bg-ok" in (task["result"] or "")


@pytest.mark.asyncio
async def test_fullstack_hitl_interrupt_before_background_start(tmp_path) -> None:
    """HITL × 后台化：interrupt_on["execute"] 按名匹配替换后的工具，
    审批发生在启动前——interrupt 时注册表无任务；批准后续跑才启动。"""
    from langgraph.types import Command

    executor = BackgroundSubagentExecutor()
    agent = _build_full_agent(
        tmp_path, executor=executor,
        script=[_execute_call("sleep 1 && echo approved-bg", run_in_background=True)],
        interrupt_on={"execute": True},
    )
    config = {"configurable": {"thread_id": "t-hitl"}}
    final_state = None
    async for chunk in agent.astream(
        {"messages": [HumanMessage(content="跑危险命令")]}, config, stream_mode="values",
    ):
        final_state = chunk
    interrupts = final_state.get("__interrupt__") if isinstance(final_state, dict) else None
    assert interrupts, "execute 调用应触发 HITL interrupt"
    # 审批发生在启动前：此刻不应有任何后台任务
    assert BackgroundSubagentExecutor.list_for_session("s-full") == []

    # 批准 → 续跑 → 工具真正执行（resume 契约与 executor.submit_decisions
    # 一致：{"decisions": [...]}）
    final_state = None
    async for chunk in agent.astream(
        Command(resume={"decisions": [{"type": "approve"}]}), config, stream_mode="values",
    ):
        final_state = chunk
    tool_msgs = [m for m in final_state["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs and "后台命令任务已启动" in tool_msgs[-1].content
    tasks = BackgroundSubagentExecutor.list_for_session("s-full")
    assert len(tasks) == 1
    task = _wait_terminal(executor, tasks[0]["task_id"])
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert "approved-bg" in (task["result"] or "")
