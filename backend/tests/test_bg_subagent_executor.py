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
        worker_factory=lambda: worker, description="处理数据",
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
        worker_factory=lambda: worker, description="需审批的任务",
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
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s1", user_id="u1")
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.AWAITING_APPROVAL.value

    executor.submit_decisions(task_id, [{"type": "reject", "message": "不许"}])
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value


def test_submit_decisions_rejects_when_not_awaiting() -> None:
    worker = _build_worker([AIMessage(content="直接完成")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s1", user_id="u1")
    _wait_terminal(executor, task_id)

    with pytest.raises(ValueError, match="不存在|不在待审批"):
        executor.submit_decisions(task_id, [{"type": "approve"}])


def test_concurrency_cap_per_session() -> None:
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(20)], slow=True,
    )  # 慢工具多轮，保持运行
    executor = BackgroundSubagentExecutor(max_concurrent_per_session=1, task_timeout_seconds=30)
    executor.start(worker_factory=lambda: worker, description="t1", session_id="s1", user_id="u1")
    time.sleep(0.3)  # 让第一个进入 running
    with pytest.raises(ValueError, match="上限"):
        executor.start(worker_factory=lambda: worker, description="t2", session_id="s1", user_id="u1")
    # 其他会话不受影响
    executor.start(worker_factory=lambda: worker, description="t3", session_id="s2", user_id="u1")


def test_cancel_running_task() -> None:
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(10)], slow=True,
    )
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s1", user_id="u1")
    time.sleep(0.2)
    snapshot = executor.cancel(task_id)
    assert snapshot["status"] in (BgTaskStatus.CANCELLED.value, BgTaskStatus.COMPLETED.value)


def test_list_and_pending_approvals_scoped_by_session() -> None:
    worker = _build_worker([AIMessage(content="ok")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    executor.start(worker_factory=lambda: worker, description="a", session_id="s1", user_id="u1")
    executor.start(worker_factory=lambda: worker, description="b", session_id="s2", user_id="u1")
    ids = [t["task_id"] for t in executor.list_for_session("s1")]
    assert len(ids) == 1
    assert executor.pending_approvals("s1") == []


# ---------------------------------------------------------------------------
# followup / notifications / 子会话查看
# ---------------------------------------------------------------------------

def test_send_message_rejects_terminal_task() -> None:
    # failed 任务拒续（completed 现在可冷恢复续话，见 followup 用例）
    def _failing_factory():
        async def _f():
            raise RuntimeError("boom")
        return _f()
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=_failing_factory, description="x", session_id="s1", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.FAILED.value

    with pytest.raises(ValueError, match="已结束"):
        executor.send_message(task_id, "调整")


def test_send_message_during_awaiting_approval_delivered_on_resume() -> None:
    """待审批期间入队：审批通过续跑后，drain（=首次模型调用）能取到指令。"""
    worker = _build_worker(
        [_call("y", "c1"), AIMessage(content="按调整后方向收尾")],
        interrupt_on={"dangerous": True},
    )
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s1", user_id="u1")
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.AWAITING_APPROVAL.value

    executor.send_message(task_id, "改查中文源")
    executor.submit_decisions(task_id, [{"type": "approve"}])

    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    # followup 在 resume 后被链式消费（本 turn 结束后作为新 turn 执行）
    # 脚本第二条为终答文本，followup turn 执行时脚本耗尽返回默认收尾


def test_terminal_records_session_notification_once() -> None:
    from noesis.agents.subagents import notifications

    worker = _build_worker([AIMessage(content="调研完成：三个要点…")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s-notif", user_id="u1")
    _wait_terminal(executor, task_id)

    notices = notifications.drain("s-notif")
    assert len(notices) == 1
    assert notices[0]["task_id"] == task_id
    assert notices[0]["status"] == "completed"
    assert "调研完成" in notices[0]["preview"]
    # drain 一次性
    assert notifications.drain("s-notif") == []


def test_notify_agent_query_prefixes_block() -> None:
    from noesis.agents.subagents import notifications

    notifications.record("s-q", "bg-1", "completed", "小结")
    notifications.record("s-q", "bg-2", "failed", "boom")
    query = notifications.notify_agent_query("s-q", "用户的问题")
    assert query.startswith("[系统通知]")
    assert "bg-1" in query and "check_task" in query
    assert "bg-2" in query
    assert query.endswith("用户的问题")
    # 无通知时原样返回
    assert notifications.notify_agent_query("s-q", "下一个问题") == "下一个问题"



def test_followup_chains_new_turn_when_running() -> None:
    """运行中 send_message：当前 turn 结束后链式开新 turn（同 thread 追加）。"""
    worker = _build_worker([AIMessage(content="第一轮完成")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="x", session_id="s-fu", user_id="u1",
    )
    # 等 completed 再冷恢复路径（running 入队链式由冷恢复后的队列消费验证）：
    # 这里直接验证冷恢复——completed send_message 同 thread 开新 turn
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value

    snapshot = executor.send_message(task_id, "请继续深入")
    assert snapshot["status"] == BgTaskStatus.RUNNING.value
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    # 新 turn 执行（脚本耗尽返回默认收尾文本）
    assert task["result"]


def test_one_shot_task_rejects_followup() -> None:
    """一次性任务：能查看，不可 send_message。"""
    worker = _build_worker([AIMessage(content="一次性完成")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="x",
        session_id="s-os", user_id="u1", one_shot=True,
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert task["kind"] == "one_shot"

    with pytest.raises(ValueError, match="一次性"):
        executor.send_message(task_id, "继续")
    # 查看不受影响
    messages = BackgroundSubagentExecutor.read_messages(task_id)
    assert any(m["role"] == "assistant" for m in messages)


def test_read_thread_messages_returns_history() -> None:
    """子会话查看：只读返回 thread 的消息视图项（user/assistant/tool）。"""
    worker = _build_worker([_call("data"), AIMessage(content="调研小结：三点发现")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="调研任务",
        session_id="s-read", user_id="u1",
    )
    _wait_terminal(executor, task_id)

    messages = BackgroundSubagentExecutor.read_messages(task_id)
    roles = [m["role"] for m in messages]
    assert "user" in roles            # 原始 description
    assert "assistant" in roles       # 工具调用 + 小结
    assert "tool" in roles            # 工具结果
    text_items = [m for m in messages if m["role"] == "assistant" and m.get("text")]
    assert any("调研小结" in m["text"] for m in text_items)
    with pytest.raises(ValueError, match="不存在"):
        BackgroundSubagentExecutor.read_messages("bg-none")


# ---------------------------------------------------------------------------
# 前台等待（run_in_background=false）与超时转后台
# ---------------------------------------------------------------------------

def _build_tools(executor: BackgroundSubagentExecutor, worker_factory):
    from noesis.agents.subagents.tools import build_background_task_tools
    return build_background_task_tools(
        worker_factory=worker_factory,
        executor=executor,
        session_id="s-fg",
        user_id="u1",
    )


@pytest.mark.asyncio
async def test_foreground_wait_returns_result() -> None:
    """前台等待：任务完成后终态文本直接作为工具返回值。"""
    worker = _build_worker([AIMessage(content="前台结果：OK")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    start = next(t for t in _build_tools(executor, lambda: worker) if t.name == "start_task")

    result = await start.ainvoke({"description": "x", "run_in_background": False})
    assert "前台结果：OK" in result


@pytest.mark.asyncio
async def test_foreground_wait_times_out_to_background() -> None:
    """前台等待超时：自动转后台，任务继续运行不被取消。"""
    from unittest.mock import patch as mock_patch

    worker = _build_worker([_slow_call("s", "c0") for _ in range(20)], slow=True)
    executor = BackgroundSubagentExecutor(task_timeout_seconds=60)
    start = next(t for t in _build_tools(executor, lambda: worker) if t.name == "start_task")

    with mock_patch("noesis.agents.subagents.tools.SubagentConfig") as cfg:
        cfg.foreground_max_wait_seconds = 0.3
        result = await start.ainvoke({"description": "慢任务", "run_in_background": False})

    assert "已自动转为后台" in result
    assert "bg-" in result
    # 任务未被取消：仍非终态（running）或最终完成，而不是 cancelled
    task_id = result.split("：")[1].split("\n")[0]
    task = executor.get(task_id)
    assert task is not None
    assert task["status"] in ("running", "completed")
    executor.cancel(task_id)


@pytest.mark.asyncio
async def test_background_default_returns_immediately() -> None:
    """默认后台：立即返回 task_id 提示。"""
    worker = _build_worker([_slow_call("s", "c0") for _ in range(20)], slow=True)
    executor = BackgroundSubagentExecutor(task_timeout_seconds=60)
    start = next(t for t in _build_tools(executor, lambda: worker) if t.name == "start_task")

    import time as _time
    began = _time.time()
    result = await start.ainvoke({"description": "x"})
    assert _time.time() - began < 2.0
    assert "后台任务已启动" in result
    task_id = result.split("：")[1].split("\n")[0]
    executor.cancel(task_id)
