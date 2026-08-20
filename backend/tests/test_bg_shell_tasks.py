"""后台命令任务（execute run_in_background）契约。

覆盖 spec「后台命令任务」Requirement：
- start_shell：不经 worker 编译，backend 执行，completed 带 exit code + 输出尾部
- one_shot 语义：send_message 拒绝；read_messages 由任务字段合成视图
- 会话沙箱销毁：运行中任务转 failed（容器回收连坐）
- execute 工具替换：同名 + run_in_background 参数；false 原样委托原工具；
  true 立即返回 task_id；超并发优雅拒绝
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.protocol import ExecuteResponse
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

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


def test_shell_task_rejects_followup_and_reads_synthetic_view() -> None:
    """one_shot 语义：send_message 拒绝；子会话视图由命令 + 结果合成。"""
    backend = _FakeShellBackend()
    executor = BackgroundSubagentExecutor()
    task_id = executor.start_shell(
        command="echo hi", backend=backend, session_id="s-sh", user_id="u1",
    )
    _wait_terminal(executor, task_id)
    with pytest.raises(ValueError, match="后台命令任务"):
        BackgroundSubagentExecutor.send_message(task_id, "再跑一次")
    messages = BackgroundSubagentExecutor.read_messages(task_id)
    assert messages[0]["role"] == "user"
    assert "echo hi" in messages[0]["text"]
    assert any(m["role"] == "assistant" and "hello" in m.get("text", "") for m in messages)


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
async def test_replace_execute_tool_rejects_when_concurrency_full() -> None:
    """超并发：返回可诊断说明（不抛异常），提示改前台执行。"""
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
    assert "启动失败" in second_content
    assert "run_in_background=false" in second_content
    executor.cancel(first_content.split("：")[1].split("\n")[0])


@pytest.mark.asyncio
async def test_replace_execute_tool_noop_without_execute_tool() -> None:
    """无 execute 工具（backend 不支持执行）：静默跳过。"""
    middleware = SimpleNamespace(tools=[])
    replace_execute_tool(
        middleware, executor=BackgroundSubagentExecutor(),
        backend=_FakeShellBackend(), session_id="s", user_id="u1",
    )
    assert middleware.tools == []
