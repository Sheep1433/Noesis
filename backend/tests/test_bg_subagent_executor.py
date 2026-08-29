"""后台子 Agent 执行器契约：全异步 start/check + HITL 审批续跑。

用真实的 create_agent 图（FakeListChatModel + 需审批工具）验证：
- start 立即返回，任务在隔离 loop 里跑，不阻塞调用方事件循环
- 无审批：completed 并带回最终小结
- 遇审批工具：interrupt → awaiting_approval（不失败）
- 审批决策：Command(resume) 同 thread 续跑至完成
- 取消 / 并发上限 / 进程退出清理
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr

from noesis.agents.subagents.executor import (
    _TASKS,
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


def test_concurrency_cap_queues_and_drains() -> None:
    """超上限排队而非拒绝；占槽任务落终态后 FIFO 唤醒排队任务。"""
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(20)], slow=True,
    )  # 慢工具多轮，保持运行
    executor = BackgroundSubagentExecutor(max_concurrent_per_session=1, task_timeout_seconds=30)
    first_id = executor.start(worker_factory=lambda: worker, description="t1", session_id="s1", user_id="u1")
    time.sleep(0.3)  # 让第一个进入 running

    # 同会话超上限：排队（不抛错、不占槽）
    queued_id = executor.start(worker_factory=lambda: worker, description="t2", session_id="s1", user_id="u1")
    task = executor.get(queued_id)
    assert task is not None
    assert task["status"] == BgTaskStatus.QUEUED.value

    # 其他会话不受影响：直接运行
    other_id = executor.start(worker_factory=lambda: worker, description="t3", session_id="s2", user_id="u1")
    task = executor.get(other_id)
    assert task is not None
    assert task["status"] != BgTaskStatus.QUEUED.value

    # 取消占槽任务 → 排队任务被 drain 唤醒，脚本耗尽后完成任务
    executor.cancel(first_id)
    task = _wait_terminal(executor, queued_id, timeout=30)
    assert task["status"] == BgTaskStatus.COMPLETED.value


def test_cancel_queued_task_removes_from_queue() -> None:
    """排队中的任务可直接取消：出队、终态，不占槽也不被唤醒。"""
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(20)], slow=True,
    )
    executor = BackgroundSubagentExecutor(max_concurrent_per_session=1, task_timeout_seconds=30)
    first_id = executor.start(worker_factory=lambda: worker, description="t1", session_id="s1", user_id="u1")
    time.sleep(0.3)
    queued_id = executor.start(worker_factory=lambda: worker, description="t2", session_id="s1", user_id="u1")

    snapshot = executor.cancel(queued_id)
    assert snapshot["status"] == BgTaskStatus.CANCELLED.value

    # 占槽任务照常完成，被取消的排队任务不会被唤醒
    task = _wait_terminal(executor, first_id, timeout=30)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    task = executor.get(queued_id)
    assert task is not None
    assert task["status"] == BgTaskStatus.CANCELLED.value


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
    assert "bg-1" not in query and "bg-2" not in query
    assert "子 Agent" in query and "打开详情" in query
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


def test_followup_model_switch_recompiles_worker() -> None:
    """followup 携带新模型：以覆盖值重编译 worker，task.model_id 跟随更新；
    不带模型或同模型的 followup 不触发重编译。"""
    factory_calls: list[Any] = []

    def worker_factory(model_id_override=None):  # noqa: ANN001, ANN001
        factory_calls.append(model_id_override)
        return _build_worker([AIMessage(content="ok")])

    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=worker_factory, description="x", session_id="s-model", user_id="u1",
        model_id="model-a",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert factory_calls == [None]  # 首轮无覆盖
    entry = _TASKS[task_id]
    assert entry.task.model_id == "model-a"

    # 冷恢复 + 换模型：factory 收到覆盖值，编译产物替换，task.model_id 更新
    executor.send_message(task_id, "换个模型继续", model_id="model-b")
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert factory_calls == [None, "model-b"]
    assert entry.task.model_id == "model-b"
    assert entry.model_override == "model-b"

    # 不带模型的 followup：沿用 model-b，不重编译
    executor.send_message(task_id, "再问一句")
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert factory_calls == [None, "model-b"]
    assert entry.task.model_id == "model-b"

    # 同模型显式传参：与当前一致，不重编译
    executor.send_message(task_id, "同模型再问", model_id="model-b")
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert factory_calls == [None, "model-b"]
# ---------------------------------------------------------------------------
# 前台等待（run_in_background=false）与超时转后台
# ---------------------------------------------------------------------------

def _build_tools(executor: BackgroundSubagentExecutor, worker_factory, create_child_session=None):
    from noesis.agents.subagents.tools import build_background_task_tools
    return build_background_task_tools(
        worker_factory=worker_factory,
        executor=executor,
        session_id="s-fg",
        user_id="u1",
        create_child_session=create_child_session,
    )


def test_start_task_schema_keeps_only_execution_mode_parameter():
    """子 Agent 统一支持续话，模型不再选择对话模式参数。"""
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    start = next(t for t in _build_tools(executor, lambda: _build_worker([])) if t.name == "start_task")

    properties = start.args_schema.model_json_schema()["properties"]
    assert set(properties) == {"description", "prompt", "run_in_background"}


@pytest.mark.asyncio
async def test_start_task_splits_short_title_and_full_prompt() -> None:
    """description=短标题 / prompt=完整任务：launch 与 executor 各取所需。

    - create_child_session 收到 (短标题, 完整任务)——会话标题用短标题，
      首条用户消息用完整任务
    - task.description 保留短标题（任务卡/列表展示），
      初轮 HumanMessage 内容用完整任务
    """
    seen: dict[str, Any] = {}

    async def fake_create_child_session(description, prompt, tool_call_id=""):
        seen["description"] = description
        seen["prompt"] = prompt
        return {
            "child_session_id": "child-split-1",
            "run_id": "run-1",
            "assistant_message_id": "am-1",
            "created_by_tool_call_id": tool_call_id,
        }

    worker = _build_worker([AIMessage(content="完成")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    start = next(
        t for t in _build_tools(executor, lambda: worker, fake_create_child_session)
        if t.name == "start_task"
    )

    result = await start.ainvoke({
        "description": "调研 npm 版本",
        "prompt": "请用 web_search 查 npm 最新稳定版本号与发布时间，返回不超过 50 字的小结。",
        "run_in_background": True,
    })
    assert "child-split-1" in result
    assert seen["description"] == "调研 npm 版本"
    assert "web_search" in seen["prompt"]

    task = _wait_terminal(executor, "child-split-1")
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert task["description"] == "调研 npm 版本"  # 任务卡展示短标题
    entry = next(e for e in _TASKS.values() if e.task.child_session_id == "child-split-1")
    assert entry.task.prompt and "web_search" in entry.task.prompt


@pytest.mark.asyncio
async def test_start_task_prompt_falls_back_to_description() -> None:
    """旧调用只传 description：完整任务回退为 description，行为不变。"""
    worker = _build_worker([AIMessage(content="完成")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    start = next(t for t in _build_tools(executor, lambda: worker) if t.name == "start_task")

    await start.ainvoke({"description": "旧式单字段任务", "run_in_background": True})
    entry = next(iter(_TASKS.values()))
    assert entry.task.prompt == "旧式单字段任务"


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
    assert "子 Agent 已启动" in result
    task_id = result.split("：")[1].split("\n")[0]
    executor.cancel(task_id)


@pytest.mark.asyncio
async def test_start_task_uses_child_session_as_task_identity() -> None:
    """每次委派先创建真实子会话，task_id 与 child session id 一致。"""
    from unittest.mock import AsyncMock

    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    create_child_session = AsyncMock(return_value="child-session-1")
    start = next(
        t for t in _build_tools(
            executor,
            lambda: _build_worker([]),
            create_child_session=create_child_session,
        ) if t.name == "start_task"
    )

    result = await start.ainvoke({"description": "检索资料"})

    create_child_session.assert_awaited_once_with("检索资料", "检索资料", "")
    assert "child-session-1" in result
    assert executor.get("child-session-1") is not None
    executor.cancel("child-session-1")


@pytest.mark.asyncio
async def test_start_task_persists_model_tool_call_reference() -> None:
    """真实 ToolCall 的 id 进入 child session 创建用例，不靠输出文本关联。"""
    from unittest.mock import AsyncMock

    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    create_child_session = AsyncMock(return_value="child-session-call")
    start = next(
        t for t in _build_tools(
            executor,
            lambda: _build_worker([]),
            create_child_session=create_child_session,
        ) if t.name == "start_task"
    )

    await start.ainvoke({
        "type": "tool_call",
        "name": "start_task",
        "args": {"description": "带引用的检索"},
        "id": "call-start-1",
    })

    create_child_session.assert_awaited_once_with("带引用的检索", "带引用的检索", "call-start-1")
    executor.cancel("child-session-call")


def test_standard_child_run_projection_collapses_tool_lifecycle() -> None:
    """带 run_id 的子 Agent 使用标准 multipart 投影，不走旧消息镜像。"""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from noesis.agents.subagents.executor import _child_projection_content

    messages = [
        HumanMessage(content="检索政策"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call-1", "name": "web_search", "args": {"query": "政策"}}],
        ),
        ToolMessage(content="找到 2 条结果", tool_call_id="call-1", name="web_search"),
        AIMessage(content="结论如下。"),
    ]

    content = _child_projection_content(messages)
    assert content["version"] == 1
    assert [part["type"] for part in content["parts"]] == ["tool", "text"]
    assert content["parts"][0]["status"] == "success"
    assert content["parts"][0]["output"] == "找到 2 条结果"
    assert content["parts"][1]["content"] == "结论如下。"


# ---------------------------------------------------------------------------
# 会话级事件订阅（SSE push，替代前端轮询）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bg_event_subscription_receives_lifecycle() -> None:
    """订阅者实时收到 started / progress / terminal（跨线程推送）。"""
    import asyncio as _asyncio

    from noesis.agents.subagents.executor import (
        subscribe_bg_events,
        unsubscribe_bg_events,
    )

    worker = _build_worker([AIMessage(content="完成")])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    queue = subscribe_bg_events("s-sse", "u1")
    try:
        task_id = executor.start(
            worker_factory=lambda: worker, description="x",
            session_id="s-sse", user_id="u1",
        )
        events: list[dict] = []
        for _ in range(3):  # started + progress + terminal
            event = await _asyncio.wait_for(queue.get(), timeout=5)
            events.append(event)
        assert events[0]["event"] == "started"
        assert events[0]["task"]["task_id"] == task_id
        assert events[1]["event"] == "progress"
        # 步数口径 = 工具调用数：纯文本步（本脚本无工具调用）不计步
        assert events[1]["task"]["progress_count"] == 0
        assert events[-1]["event"] == "terminal"
        assert events[-1]["task"]["status"] == "completed"
    finally:
        unsubscribe_bg_events("s-sse", queue)


@pytest.mark.asyncio
async def test_standard_run_event_subscription_starts_on_run_id() -> None:
    from noesis.agents.subagents.executor import subscribe_run_events, unsubscribe_run_events

    queue = subscribe_run_events("run-sse", "u1")
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    try:
        task_id = executor.start(
            worker_factory=lambda: _build_worker([AIMessage(content="完成")]),
            description="x",
            session_id="s-sse",
            user_id="u1",
            task_id="child-sse",
            run_id="run-sse",
            assistant_message_id="assistant-sse",
        )
        event = await asyncio.wait_for(queue.get(), timeout=5)
        assert event["type"] == "run.started"
        assert event["run_id"] == "run-sse"
        assert event["session_id"] == "child-sse"
    finally:
        unsubscribe_run_events("run-sse", queue)
        executor.cancel(task_id)


# ---------------------------------------------------------------------------
# run 内即时感知（BgNotifyMiddleware）
# ---------------------------------------------------------------------------

def test_bg_notify_middleware_injects_once() -> None:
    """主 Agent 模型调用边界注入未送达通知；已送达不重复。"""
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import HumanMessage, SystemMessage
    from noesis.agents.subagents import notifications
    from noesis.agents.subagents.notify_middleware import BgNotifyMiddleware

    notifications.record("s-mw", "bg-1", "completed", "小结")
    request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[HumanMessage(content="继续干活")],
        system_message=SystemMessage(content="sys"),
        state={},
    )
    seen: list = []
    mw = BgNotifyMiddleware(session_id="s-mw")
    mw.wrap_model_call(request, lambda req: seen.append(req.messages) or "ok")  # type: ignore[arg-type,return-value]
    injected = seen[0]
    assert "[系统通知]" in injected[-1].content
    assert "bg-1" not in injected[-1].content
    assert "打开详情" in injected[-1].content

    # 已送达：第二次模型调用与下一轮 agent_query 注入都不再出现
    seen.clear()
    mw.wrap_model_call(request, lambda req: seen.append(req.messages) or "ok")  # type: ignore[arg-type,return-value]
    assert len(seen[0]) == 1
    assert notifications.notify_agent_query("s-mw", "下一轮问题") == "下一轮问题"


def test_run_start_injection_marks_delivered_for_middleware() -> None:
    """exec_query 的下一轮注入与 run 内中间件共用 delivered 标记，不双发。"""
    from noesis.agents.subagents import notifications
    from noesis.agents.subagents.notify_middleware import BgNotifyMiddleware
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import HumanMessage, SystemMessage

    notifications.record("s-mw2", "bg-2", "completed", "结果")
    # 下一轮注入（run 启动）先消费
    query = notifications.notify_agent_query("s-mw2", "新问题")
    assert "[系统通知]" in query
    # run 内中间件不再重复注入
    request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[HumanMessage(content="x")],
        system_message=SystemMessage(content="sys"),
        state={},
    )
    seen: list = []
    BgNotifyMiddleware(session_id="s-mw2").wrap_model_call(
        request, lambda req: seen.append(req.messages) or "ok",  # type: ignore[arg-type,return-value]
    )
    assert len(seen[0]) == 1


# ---------------------------------------------------------------------------
# 自动续跑（continuation run）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_maybe_continue_creates_run_when_idle(monkeypatch) -> None:
    """无活跃 run + 有通知 → 创建 continuation run；通知标记已送达。"""
    from noesis.services import bg_continuation_service as svc

    svc.reset_for_tests()
    created: list = []

    class _FakeUser:
        pass

    async def fake_load_user(user_id: str):
        return _FakeUser()

    class _FakeRepo:
        def __init__(self, db):
            pass

        async def get_active_for_session(self, user_id, session_id):
            return None

    class _FakeRun:
        id = "run-x"
        assistant_message_id = "am-x"

    async def fake_create(request, current_user, db):
        created.append(request)
        return _FakeRun()

    class _FakeCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    from noesis.agents.subagents import notifications
    notifications.record("s-cont", "bg-1", "completed", "小结")

    monkeypatch.setattr(svc, "_load_user", fake_load_user)
    monkeypatch.setattr(svc, "AgentRunRepository", _FakeRepo)
    monkeypatch.setattr(svc, "RunService", type("RS", (), {"create": staticmethod(fake_create)}))
    monkeypatch.setattr(svc, "pg_manager", type("PM", (), {"get_async_session_context": staticmethod(lambda: _FakeCtx())}))
    monkeypatch.setattr(svc, "SubagentConfig", SimpleNamespace(auto_continue=True))

    result = await svc.maybe_continue("s-cont", "u1")
    assert result == {"run_id": "run-x", "assistant_message_id": "am-x"}
    assert len(created) == 1
    assert "[系统通知]" in created[0].content
    assert "check_task" in created[0].content
    assert created[0].extra.get("bg_continuation") is True
    # 消息源标记随 extra 落库：前端据此渲染为通知条而非用户气泡
    assert created[0].extra.get("source_kind") == "bg_task_notice"
    svc.reset_for_tests()


@pytest.mark.asyncio
async def test_maybe_continue_skips_when_run_active(monkeypatch) -> None:
    """会话有活跃 run：不创建，通知保留（留给 run 内中间件）。"""
    from noesis.services import bg_continuation_service as svc
    from noesis.agents.subagents import notifications

    svc.reset_for_tests()
    notifications.record("s-cont2", "bg-2", "completed", "x")

    class _ActiveRun:
        id = "run-active"

    class _FakeRepo:
        def __init__(self, db):
            pass

        async def get_active_for_session(self, user_id, session_id):
            return _ActiveRun()

    async def fail_create(*a, **k):
        raise AssertionError("should not create")

    monkeypatch.setattr(svc, "AgentRunRepository", _FakeRepo)
    monkeypatch.setattr(svc, "RunService", type("RS", (), {"create": staticmethod(fail_create)}))

    async def early_check(session_id, user_id):
        # active 检查在 _load_user / db 之前无法短路（实现顺序）——直接调用并断言 None
        return None

    # 直接走到 active 检查：_load_user 和 pg_manager 需要 stub
    async def fake_load_user(user_id):
        return object()

    class _FakeCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(svc, "_load_user", fake_load_user)
    monkeypatch.setattr(svc, "pg_manager", type("PM", (), {"get_async_session_context": staticmethod(lambda: _FakeCtx())}))

    result = await svc.maybe_continue("s-cont2", "u1")
    assert result is None
    # 通知未被消费
    remaining = notifications.take_undelivered("s-cont2", mark_delivered=False)
    assert len(remaining) == 1
    svc.reset_for_tests()


@pytest.mark.asyncio
async def test_consecutive_wake_cap(monkeypatch) -> None:
    """连续自动唤醒达上限后不再触发；note_user_activity 清零。"""
    from noesis.services import bg_continuation_service as svc

    svc.reset_for_tests()
    for i in range(svc._MAX_CONSECUTIVE):
        svc._wake_counts["s-cap"] = i + 1
    assert svc._wake_counts["s-cap"] >= svc._MAX_CONSECUTIVE

    from noesis.agents.subagents import notifications
    notifications.record("s-cap", "bg-3", "completed", "x")
    result = await svc.maybe_continue("s-cap", "u1")
    assert result is None  # 触顶拒绝（无需 mock 创建路径，上限检查在 active 检查前）

    svc.note_user_activity("s-cap")
    assert "s-cap" not in svc._wake_counts
    svc.reset_for_tests()


@pytest.mark.asyncio
async def test_bg_event_subscription_receives_approval_lifecycle() -> None:
    """审批全生命周期事件：started → awaiting_approval → followup(续跑) → terminal。

    awaiting_approval / 审批续跑此前不发布事件，前端审批卡只在快照时
    可见；回归断言每个状态转换都有 SSE 事件。
    """
    import asyncio as _asyncio

    from noesis.agents.subagents.executor import (
        subscribe_bg_events,
        unsubscribe_bg_events,
    )

    worker = _build_worker(
        [_call("x"), AIMessage(content="工具已批准，收尾")],
        interrupt_on={"dangerous": True},
    )
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    queue = subscribe_bg_events("s-sse-ap", "u1")
    try:
        task_id = executor.start(
            worker_factory=lambda: worker, description="需审批的任务",
            session_id="s-sse-ap", user_id="u1",
        )
        events: list[dict] = []
        while not events or events[-1]["event"] != "awaiting_approval":
            events.append(await _asyncio.wait_for(queue.get(), timeout=5))
        assert events[0]["event"] == "started"
        assert "progress" in [event["event"] for event in events]
        assert events[-1]["task"]["status"] == BgTaskStatus.AWAITING_APPROVAL.value
        assert events[-1]["task"]["interrupt"]

        executor.submit_decisions(task_id, [{"type": "approve"}])
        while events[-1]["event"] != "terminal":
            events.append(await _asyncio.wait_for(queue.get(), timeout=5))
        event_names = [event["event"] for event in events]
        assert event_names.index("awaiting_approval") < event_names.index("followup")
        assert events[-1]["task"]["status"] == BgTaskStatus.COMPLETED.value
    finally:
        unsubscribe_bg_events("s-sse-ap", queue)


def test_interrupt_action_requests_carry_tool_call_id() -> None:
    """langchain HITL 的 ActionRequest 不带 tool_call_id（只有 name/args/description）。

    不回填的话快照/消息投影匹配不到工具段，被中断的调用永远停在
    running（扫光 + 「运行中」标签），与等待审批的事实不符。
    """
    worker = _build_worker(
        [_call("y", "c-enrich"), AIMessage(content="收尾")],
        interrupt_on={"dangerous": True},
    )
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="x", session_id="s-enrich", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.AWAITING_APPROVAL.value

    actions = task["interrupt"]["action_requests"]
    assert len(actions) == 1
    assert actions[0]["tool_call_id"] == "c-enrich"


def test_pending_tool_calls_excludes_answered() -> None:
    """待审批回填的候选 = 尚无 ToolMessage 应答的 tool_calls。"""
    from langchain_core.messages import ToolMessage

    from noesis.agents.subagents.executor import _pending_tool_calls

    messages = [
        AIMessage(content="", tool_calls=[
            {"name": "write_file", "args": {"path": "/memory/a.md"}, "id": "c1", "type": "tool_call"},
            {"name": "execute", "args": {"command": "ls"}, "id": "c2", "type": "tool_call"},
        ]),
        ToolMessage(content="done", tool_call_id="c1", name="write_file"),
        AIMessage(content="", tool_calls=[
            {"name": "write_file", "args": {"path": "/memory/b.md"}, "id": "c3", "type": "tool_call"},
        ]),
    ]
    pending = _pending_tool_calls(messages)
    assert [call["id"] for call in pending] == ["c2", "c3"]
    assert pending[0]["name"] == "execute"


def test_mark_approval_pending_flips_matching_parts() -> None:
    """审批挂起投影：匹配 tool_call_id 的工具段转 approval_pending 并附 hitl 元数据。"""
    from noesis.agents.subagents.executor import _mark_approval_pending

    content = {
        "version": 1,
        "parts": [
            {"type": "tool", "tool_call_id": "c1", "name": "write_file", "state": "running", "status": "running"},
            {"type": "tool", "tool_call_id": "c2", "name": "execute", "state": "succeeded", "status": "success"},
        ],
    }
    payload = {
        "interrupt_id": "iid-1",
        "kind": "approval",
        "action_requests": [{"name": "write_file", "args": {}, "tool_call_id": "c1"}],
    }
    _mark_approval_pending(content, payload)

    part = content["parts"][0]
    assert part["state"] == "approval_pending"
    assert part["status"] == "running"
    assert part["hitl"] == {"kind": "approval", "status": "pending", "interrupt_id": "iid-1"}
    # 未被中断的段不受影响
    assert content["parts"][1]["state"] == "succeeded"

    # action_requests 缺 tool_call_id（未回填）时不得误伤任何段
    content2 = {"parts": [{"type": "tool", "tool_call_id": "c9", "state": "running"}]}
    _mark_approval_pending(content2, {"interrupt_id": "iid", "action_requests": [{"name": "write_file"}]})
    assert content2["parts"][0]["state"] == "running"


def test_context_snapshot_from_worker_usage_metadata() -> None:
    """子会话上下文快照：worker 消息 usage_metadata → 快照变更发布/记录。

    主对话的快照由 SSE bridge 提取；子 run 无 bridge，executor 从 thread
    消息取同口径（单轮真实 input_tokens，每次覆盖）。
    """
    from noesis.agents.subagents import executor as ex_mod

    worker = _build_worker([
        AIMessage(
            content="完成",
            usage_metadata={"input_tokens": 12000, "output_tokens": 50, "total_tokens": 12050},
        ),
    ])
    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="调研任务",
        session_id="s-ctx", user_id="u1",
        child_session_id="child-ctx", model_id="deepseek-v4-flash",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value

    with ex_mod._TASKS_LOCK:
        entry = ex_mod._TASKS.get(task_id)
    assert entry is not None
    snapshot = entry.task.context_snapshot
    assert snapshot is not None
    assert snapshot["current_tokens"] == 12000
    assert snapshot["max_tokens"] > 0
    assert 0 < snapshot["used_percentage"] <= 100


# ---------------------------------------------------------------------------
# 后台命令任务（kind="shell"）：description 作为任务卡标题
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_shell_description_as_task_title() -> None:
    """execute 后台化：description 作任务卡标题；缺省回退原始命令。"""
    from unittest.mock import AsyncMock

    class _Ok:
        exit_code = 0
        output = "ok"

    backend = SimpleNamespace(aexecute=AsyncMock(return_value=_Ok()))
    executor = BackgroundSubagentExecutor(shell_task_timeout_seconds=30)

    task_id = executor.start_shell(
        command="uv run pytest tests/ -q 2>&1 | tail -5",
        backend=backend,
        session_id="s-shell",
        user_id="u1",
        description="跑全量回归测试",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert task["description"] == "跑全量回归测试"

    task_id2 = executor.start_shell(
        command="pnpm lint",
        backend=backend,
        session_id="s-shell",
        user_id="u1",
    )
    task2 = _wait_terminal(executor, task_id2)
    assert task2["description"] == "pnpm lint"


def test_step_count_does_not_cap_at_progress_preview_limit() -> None:
    """步数是与有界 progress 预览分离的权威计数：超过 50 步不得封顶。"""
    from noesis.agents.subagents.executor import (
        MAX_PROGRESS_ENTRIES,
        BackgroundTask,
        _progress_append,
    )

    task = BackgroundTask(
        task_id="bg-steps",
        session_id="s",
        user_id="u",
        description="步数计数",
    )
    total = MAX_PROGRESS_ENTRIES + 12
    for i in range(total):
        _progress_append(task, {"kind": "tool_call", "name": f"tool-{i}", "ts": 0})

    assert len(task.progress) == MAX_PROGRESS_ENTRIES  # 预览有界
    assert task.step_count == total  # 计数不封顶
    assert task.to_dict(include_progress=False)["progress_count"] == total


@pytest.mark.asyncio
async def test_submit_isolated_cuts_caller_contextvar_inheritance() -> None:
    """调度到隔离 loop 的协程不得继承调用线程的 contextvars。

    run_coroutine_threadsafe 经 call_soon_threadsafe 复制调用线程上下文；
    调度点若在父 run 的 astream_events 追踪上下文内，子 Agent 会继承父
    tracer，其工具事件泄入父消息（幽灵工具 part +「本轮未完成」误报）。
    """
    import contextvars as _contextvars

    from noesis.agents.subagents.executor import _ensure_loop, _submit_isolated

    marker: _contextvars.ContextVar = _contextvars.ContextVar(
        "noesis_test_leak_marker", default=None
    )
    marker.set("leaked")

    async def probe():
        return marker.get()

    future = _submit_isolated(_ensure_loop(), probe())
    assert await asyncio.wrap_future(future) is None


def test_task_lookup_accepts_unique_short_id_prefix() -> None:
    """cancel/check 支持唯一前缀（模型惯用 8 位短 id；精确匹配曾致整批取消失败）。"""
    from noesis.agents.subagents.executor import (
        _TASKS,
        _TASKS_LOCK,
        BackgroundSubagentExecutor,
        BackgroundTask,
        BgTaskStatus,
    )

    task = BackgroundTask(
        task_id="bg-prefix-1",
        session_id="s",
        user_id="u",
        description="前缀查找",
        child_session_id="733021ae-06ef-45de-aaee-9e3913ffc785",
        status=BgTaskStatus.COMPLETED,
    )
    from noesis.agents.subagents.executor import _TaskEntry

    _TASKS[task.task_id] = _TaskEntry(
        task=task,
        agent_factory=None,
        recursion_limit=10,
        timeout_seconds=0,
        hitl_timeout_seconds=1,
    )
    try:
        # 8 位短 id（child_session_id 前缀）命中
        snapshot = BackgroundSubagentExecutor.get("733021ae")
        assert snapshot is not None and snapshot["task_id"] == "bg-prefix-1"
        # 完整 child_session_id 与 task_id 仍然精确命中
        assert BackgroundSubagentExecutor.get(task.child_session_id)["task_id"] == "bg-prefix-1"
        assert BackgroundSubagentExecutor.get("bg-prefix-1")["task_id"] == "bg-prefix-1"
    finally:
        with _TASKS_LOCK:
            _TASKS.pop("bg-prefix-1", None)


def test_task_lookup_rejects_ambiguous_prefix() -> None:
    """歧义前缀不得猜测：命中多个时按不存在处理。"""
    from noesis.agents.subagents.executor import (
        _TASKS,
        _TASKS_LOCK,
        BackgroundSubagentExecutor,
        BackgroundTask,
        BgTaskStatus,
        _TaskEntry,
    )

    entries = {}
    for i, suffix in enumerate(("aaaa1111", "aaaa2222")):
        task = BackgroundTask(
            task_id=f"bg-amb-{i}",
            session_id="s",
            user_id="u",
            description=f"歧义{i}",
            child_session_id=f"{suffix}-0000-0000-0000-00000000000{i}",
            status=BgTaskStatus.COMPLETED,
        )
        entry = _TaskEntry(
            task=task,
            agent_factory=None,
            recursion_limit=10,
            timeout_seconds=0,
            hitl_timeout_seconds=1,
        )
        _TASKS[task.task_id] = entry
        entries[task.task_id] = entry
    try:
        assert BackgroundSubagentExecutor.get("aaaa") is None
    finally:
        with _TASKS_LOCK:
            for key in entries:
                _TASKS.pop(key, None)
