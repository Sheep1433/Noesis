"""后台子 Agent 执行器契约：全异步 start/check + HITL 审批续跑。

用真实的 create_agent 图（FakeListChatModel + 需审批工具）验证：
- start 立即返回，任务在隔离 loop 里跑，不阻塞调用方事件循环
- 无审批：completed 并带回最终小结
- 遇审批工具：interrupt → awaiting_approval（不失败）
- 审批决策：Command(resume) 同 thread 续跑至完成
- 取消 / 并发上限 / 进程退出清理
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr

from noesis.agents.subagents.executor import (
    BackgroundSubagentExecutor,
    BgTaskStatus,
    shutdown as bg_shutdown,
)


class _ScriptedToolModel(BaseChatModel):
    """按脚本依次返回 AIMessage（可带 tool_calls）；bind_tools 返回自身。

    后台任务在隔离线程跑，取脚本用锁保证线程安全；脚本耗尽后返回收尾文本。
    """

    script: list[AIMessage]
    _cursor: int = PrivateAttr(default=0)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        with self._lock:
            idx = self._cursor
            self._cursor += 1
        message = (
            self.script[idx]
            if idx < len(self.script)
            else AIMessage(content="任务完成")
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _call(value: str, call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "dangerous", "args": {"value": value}, "id": call_id, "type": "tool_call"}],
    )


def _dangerous_tool():
    @tool
    def dangerous(value: str) -> str:
        """A tool that requires approval."""
        return f"done:{value}"

    return dangerous


def _slow_tool():
    @tool
    def slow(value: str) -> str:
        """A tool that takes a while, keeping the agent running."""
        time.sleep(0.6)
        return f"slow:{value}"

    return slow


def _slow_call(value: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "slow", "args": {"value": value}, "id": call_id, "type": "tool_call"}],
    )


def _build_worker(script: list[AIMessage], *, interrupt_on: dict | None = None, slow: bool = False) -> Any:
    middleware = []
    if interrupt_on:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
    return create_agent(
        _ScriptedToolModel(script=script),
        tools=[_slow_tool()] if slow else [_dangerous_tool()],
        middleware=middleware,
        checkpointer=MemorySaver(),
        name="task-worker",
    )


def _wait_terminal(executor: BackgroundSubagentExecutor, task_id: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = executor.get(task_id)
        assert task is not None
        if task["status"] == BgTaskStatus.AWAITING_APPROVAL.value or _is_terminal(task):
            return task
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} 未在 {timeout}s 内到达稳定状态")


def _is_terminal(task: dict[str, Any]) -> bool:
    return task["status"] in {
        BgTaskStatus.COMPLETED.value,
        BgTaskStatus.FAILED.value,
        BgTaskStatus.CANCELLED.value,
        BgTaskStatus.TIMED_OUT.value,
    }


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    bg_shutdown()


def test_start_returns_immediately_and_completes() -> None:
    worker = _build_worker([_call("data"), AIMessage(content="任务完成：已处理")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)

    started = time.time()
    task_id = executor.start(
        agent=worker, description="处理数据",
        session_id="s1", user_id="u1",
    )
    assert task_id.startswith("bg-")
    # start 不等执行完成（FakeList 模型毫秒级跑完，放宽到 2s 内即视为非阻塞语义成立）
    assert time.time() - started < 2.0

    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert task["result"]
    assert task["session_id"] == "s1"
    assert task["user_id"] == "u1"


def test_approval_interrupt_pauses_then_resume_completes() -> None:
    worker = _build_worker(
        [_call("x"), AIMessage(content="工具已批准，收尾")],
        interrupt_on={"dangerous": True},
    )
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)

    task_id = executor.start(
        agent=worker, description="需审批的任务",
        session_id="s1", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.AWAITING_APPROVAL.value
    assert task["interrupt"]
    assert task["interrupt"]["action_requests"]

    snapshot = executor.submit_decisions(task_id, [{"type": "approve"}])
    assert snapshot["status"] == BgTaskStatus.RUNNING.value

    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert "收尾" in task["result"] or task["result"]


def test_reject_decision_resumes_and_completes() -> None:
    worker = _build_worker(
        [_call("y"), AIMessage(content="被拒绝，改用直接回答")],
        interrupt_on={"dangerous": True},
    )
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(agent=worker, description="x", session_id="s1", user_id="u1")
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.AWAITING_APPROVAL.value

    executor.submit_decisions(task_id, [{"type": "reject", "message": "不许"}])
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value


def test_submit_decisions_rejects_when_not_awaiting() -> None:
    worker = _build_worker([AIMessage(content="直接完成")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(agent=worker, description="x", session_id="s1", user_id="u1")
    _wait_terminal(executor, task_id)

    with pytest.raises(ValueError, match="不存在|不在待审批"):
        executor.submit_decisions(task_id, [{"type": "approve"}])


def test_concurrency_cap_per_session() -> None:
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(20)], slow=True,
    )  # 慢工具多轮，保持运行
    executor = BackgroundSubagentExecutor(max_concurrent_per_session=1, task_timeout_seconds=30)
    executor.start(agent=worker, description="t1", session_id="s1", user_id="u1")
    time.sleep(0.3)  # 让第一个进入 running
    with pytest.raises(ValueError, match="上限"):
        executor.start(agent=worker, description="t2", session_id="s1", user_id="u1")
    # 其他会话不受影响
    executor.start(agent=worker, description="t3", session_id="s2", user_id="u1")


def test_cancel_running_task() -> None:
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(10)], slow=True,
    )
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(agent=worker, description="x", session_id="s1", user_id="u1")
    time.sleep(0.2)
    snapshot = executor.cancel(task_id)
    assert snapshot["status"] in (BgTaskStatus.CANCELLED.value, BgTaskStatus.COMPLETED.value)


def test_list_and_pending_approvals_scoped_by_session() -> None:
    worker = _build_worker([AIMessage(content="ok")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    executor.start(agent=worker, description="a", session_id="s1", user_id="u1")
    executor.start(agent=worker, description="b", session_id="s2", user_id="u1")
    ids = [t["task_id"] for t in executor.list_for_session("s1")]
    assert len(ids) == 1
    assert executor.pending_approvals("s1") == []
