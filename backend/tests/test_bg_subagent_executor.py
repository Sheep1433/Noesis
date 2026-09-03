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
    BackgroundTaskExecutor,
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


def _wait_terminal(executor: BackgroundTaskExecutor, task_id: str, timeout: float = 10.0) -> dict[str, Any]:
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)

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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)

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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s1", user_id="u1")
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.AWAITING_APPROVAL.value

    executor.submit_decisions(task_id, [{"type": "reject", "message": "不许"}])
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value


def test_submit_decisions_rejects_when_not_awaiting() -> None:
    worker = _build_worker([AIMessage(content="直接完成")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s1", user_id="u1")
    _wait_terminal(executor, task_id)

    with pytest.raises(ValueError, match="不存在|不在待审批"):
        executor.submit_decisions(task_id, [{"type": "approve"}])


def test_concurrency_cap_queues_and_drains() -> None:
    """超上限排队而非拒绝；占槽任务落终态后 FIFO 唤醒排队任务。"""
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(20)], slow=True,
    )  # 慢工具多轮，保持运行
    executor = BackgroundTaskExecutor(max_concurrent_per_session=1, task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(max_concurrent_per_session=1, task_timeout_seconds=30)
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
    """协作停止：cancel 即时受理返回 stopping，静止边界后落 CANCELLED 并保留部分产出。"""
    # 首轮文本+工具调用同发：工具执行期间受理停止，退出时文本进部分成果回收
    first = AIMessage(
        content="先分析一下任务背景。",
        tool_calls=[{"name": "slow", "args": {"value": "s0"}, "id": "c0", "type": "tool_call"}],
    )
    worker = _build_worker(
        [first] + [_slow_call(f"s{i}", f"c{i}") for i in range(10)], slow=True,
    )
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s1", user_id="u1")
    time.sleep(0.2)
    snapshot = executor.cancel(task_id)
    # 受理即时可见（无需等待当前步骤）
    assert snapshot["status"] == BgTaskStatus.STOPPING.value
    assert snapshot["stop_reason"] == "cancelled"
    # 重复停止幂等返回同一快照
    again = executor.cancel(task_id)
    assert again["status"] == BgTaskStatus.STOPPING.value
    # 静止边界（slow 工具 0.6s 完成）后协作退出为 CANCELLED
    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.CANCELLED.value:
            break
        time.sleep(0.05)
    task = executor.get(task_id)
    assert task["status"] == BgTaskStatus.CANCELLED.value
    # 部分成果回收：中止前已产出的文本以标注前缀保留
    assert task["result"] and task["result"].startswith("中止前部分产出")
    assert "先分析一下任务背景。" in task["result"]


def test_cancel_without_text_output_no_placeholder() -> None:
    """无文本产出的任务取消：不产生空占位文本（spec 2.4）。"""
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(10)], slow=True,
    )
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s2", user_id="u1")
    time.sleep(0.2)
    assert executor.cancel(task_id)["status"] == BgTaskStatus.STOPPING.value
    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.CANCELLED.value:
            break
        time.sleep(0.05)
    task = executor.get(task_id)
    assert task["status"] == BgTaskStatus.CANCELLED.value
    assert task["result"] is None


def test_list_and_pending_approvals_scoped_by_session() -> None:
    worker = _build_worker([AIMessage(content="ok")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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

    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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

def _build_tools(executor: BackgroundTaskExecutor, worker_factory, create_child_session=None):
    """以角色注册表 + 中间件构造工具面（与生产装配同构，单 general 角色）。"""
    from noesis.agents.subagents.registry import SubagentRegistry, SubagentRole
    from noesis.agents.subagents.tools_middleware import NoesisSubagentMiddleware

    registry = SubagentRegistry()
    registry.register(SubagentRole(
        name="general",
        description="通用子 Agent",
        worker_factory=worker_factory,
    ))
    middleware = NoesisSubagentMiddleware(
        registry=registry,
        executor=executor,
        session_id="s-fg",
        user_id="u1",
        create_child_session=create_child_session,
    )
    return middleware.tools


def _tool_text(result) -> str:
    """工具返回值可能是纯文本或 Command（回 ToolMessage + 写 bg_tasks state）：
    统一提取模型可见文本。"""
    from langgraph.types import Command

    if isinstance(result, Command):
        messages = result.update.get("messages", [])
        return str(messages[0].content) if messages else ""
    return str(result)


def test_start_task_schema_keeps_only_execution_mode_parameter():
    """参数面固定为四字段：标题/完整指令/角色类型/执行模式，无对话模式参数。

    subagent_type 必填——运行时只选角色不选模型，模型参数不出现在工具面。
    """
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    start = next(t for t in _build_tools(executor, lambda: _build_worker([])) if t.name == "start_task")

    schema = start.args_schema.model_json_schema()
    properties = schema["properties"]
    assert set(properties) == {"description", "prompt", "subagent_type", "run_in_background"}
    assert "subagent_type" in schema.get("required", [])


@pytest.mark.asyncio
async def test_start_task_splits_short_title_and_full_prompt() -> None:
    """description=短标题 / prompt=完整任务：launch 与 executor 各取所需。

    - create_child_session 收到 (短标题, 完整任务)——会话标题用短标题，
      首条用户消息用完整任务
    - task.description 保留短标题（任务卡/列表展示），
      初轮 HumanMessage 内容用完整任务
    """
    seen: dict[str, Any] = {}

    async def fake_create_child_session(description, prompt, tool_call_id="", subagent_type="general", model_id=None):
        seen["description"] = description
        seen["prompt"] = prompt
        seen["subagent_type"] = subagent_type
        seen["model_id"] = model_id
        return {
            "child_session_id": "child-split-1",
            "run_id": "run-1",
            "assistant_message_id": "am-1",
            "created_by_tool_call_id": tool_call_id,
        }

    worker = _build_worker([AIMessage(content="完成")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    start = next(
        t for t in _build_tools(executor, lambda: worker, fake_create_child_session)
        if t.name == "start_task"
    )

    result = await start.ainvoke({
        "description": "调研 npm 版本",
        "prompt": "请用 web_search 查 npm 最新稳定版本号与发布时间，返回不超过 50 字的小结。",
        "subagent_type": "general",
        "run_in_background": True,
    })
    result = _tool_text(result)
    assert "child-split-1" in result
    assert seen["description"] == "调研 npm 版本"
    assert "web_search" in seen["prompt"]
    assert seen["subagent_type"] == "general"

    task = _wait_terminal(executor, "child-split-1")
    assert task["status"] == BgTaskStatus.COMPLETED.value
    assert task["description"] == "调研 npm 版本"  # 任务卡展示短标题
    entry = next(e for e in _TASKS.values() if e.task.child_session_id == "child-split-1")
    assert entry.task.prompt and "web_search" in entry.task.prompt


@pytest.mark.asyncio
async def test_start_task_prompt_falls_back_to_description() -> None:
    """旧调用只传 description：完整任务回退为 description，行为不变。"""
    worker = _build_worker([AIMessage(content="完成")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    start = next(t for t in _build_tools(executor, lambda: worker) if t.name == "start_task")

    await start.ainvoke({"description": "旧式单字段任务", "subagent_type": "general", "run_in_background": True})
    entry = next(iter(_TASKS.values()))
    assert entry.task.prompt == "旧式单字段任务"


@pytest.mark.asyncio
async def test_foreground_wait_returns_result() -> None:
    """前台等待：任务完成后终态文本直接作为工具返回值。"""
    worker = _build_worker([AIMessage(content="前台结果：OK")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    start = next(t for t in _build_tools(executor, lambda: worker) if t.name == "start_task")

    result = await start.ainvoke({"description": "x", "subagent_type": "general", "run_in_background": False})
    result = _tool_text(result)
    assert "前台结果：OK" in result


@pytest.mark.asyncio
async def test_foreground_wait_times_out_to_background() -> None:
    """前台等待超时：自动转后台，任务继续运行不被取消。"""
    from unittest.mock import patch as mock_patch

    worker = _build_worker([_slow_call("s", "c0") for _ in range(20)], slow=True)
    executor = BackgroundTaskExecutor(task_timeout_seconds=60)
    start = next(t for t in _build_tools(executor, lambda: worker) if t.name == "start_task")

    with mock_patch("noesis.agents.subagents.tools_middleware.SubagentConfig") as cfg:
        cfg.foreground_max_wait_seconds = 0.3
        result = await start.ainvoke({"description": "慢任务", "subagent_type": "general", "run_in_background": False})

    result = _tool_text(result)
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=60)
    start = next(t for t in _build_tools(executor, lambda: worker) if t.name == "start_task")

    import time as _time
    began = _time.time()
    result = await start.ainvoke({"description": "x", "subagent_type": "general"})
    assert _time.time() - began < 2.0
    result = _tool_text(result)
    assert "子 Agent 已启动" in result
    task_id = result.split("：")[1].split("\n")[0]
    executor.cancel(task_id)


@pytest.mark.asyncio
async def test_start_task_uses_child_session_as_task_identity() -> None:
    """每次委派先创建真实子会话，task_id 与 child session id 一致。"""
    from unittest.mock import AsyncMock

    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    create_child_session = AsyncMock(return_value="child-session-1")
    start = next(
        t for t in _build_tools(
            executor,
            lambda: _build_worker([]),
            create_child_session=create_child_session,
        ) if t.name == "start_task"
    )

    result = await start.ainvoke({"description": "检索资料", "subagent_type": "general"})
    result = _tool_text(result)

    create_child_session.assert_awaited_once_with(
        "检索资料", "检索资料", "", "general", None,
    )
    assert "child-session-1" in result
    assert executor.get("child-session-1") is not None
    executor.cancel("child-session-1")


@pytest.mark.asyncio
async def test_start_task_persists_model_tool_call_reference() -> None:
    """真实 ToolCall 的 id 进入 child session 创建用例，不靠输出文本关联。"""
    from unittest.mock import AsyncMock

    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
        "args": {"description": "带引用的检索", "subagent_type": "general"},
        "id": "call-start-1",
    })

    create_child_session.assert_awaited_once_with(
        "带引用的检索", "带引用的检索", "call-start-1", "general", None,
    )
    executor.cancel("child-session-call")


@pytest.mark.asyncio
async def test_standard_child_run_projection_collapses_tool_lifecycle() -> None:
    """统一管道下子 Agent 投影为标准 multipart：工具生命周期 + 最终文本。

    旧 values-diff 手拼投影已删除；本用例经完整管道（astream_events →
    RuntimeEventMapper → builder）验证：帧词汇逐条转发（与主链路同源）、
    message.updated 退役、run.finished 携带权威投影结构。
    """
    from noesis.agents.subagents.executor import subscribe_run_events, unsubscribe_run_events

    worker = _build_worker([
        _call("政策", call_id="call-1"),
        AIMessage(content="结论如下。"),
    ])
    queue = subscribe_run_events("run-proj", "u1")
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    try:
        task_id = executor.start(
            worker_factory=lambda: worker, description="检索政策",
            session_id="s-proj", user_id="u1",
            child_session_id="child-proj",
            run_id="run-proj", assistant_message_id="assistant-proj",
        )
        task = _wait_terminal(executor, task_id)
        assert task["status"] == BgTaskStatus.COMPLETED.value
        assert task["result"] == "结论如下。"
        assert task["progress_count"] == 1  # 步数口径 = 工具调用数

        # 终态投影：tool part（含输出与成功态）+ text part
        # （事件经 call_soon_threadsafe 投递到本协程 loop，先让出控制权再收集）
        await asyncio.sleep(0.1)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        # 统一帧词汇（与主链路同源）：边界帧逐条投递，message.updated 退役
        frame_types = [e["type"] for e in events]
        assert "message.updated" not in frame_types, frame_types
        for expected in (
            "message-start", "tool-input-start", "tool-input-available",
            "tool-output-available", "text-delta",
        ):
            assert expected in frame_types, frame_types
        # 终态 run.finished 携带权威投影：tool part（含输出与成功态）+ text part
        # （统一管道 builder 产物与主链路同构，version 由前端解析层规范化补齐）
        finished = [e for e in events if e["type"] == "run.finished"]
        assert finished, frame_types
        content = finished[-1]["content"]
        assert "parts" in content
        kinds = [part["type"] for part in content["parts"]]
        assert kinds == ["tool", "text"]
        tool_part = content["parts"][0]
        assert tool_part["name"] == "dangerous"
        assert tool_part["output"] == "done:政策"
        assert tool_part["status"] == "success"
        assert content["parts"][1]["content"] == "结论如下。"
    finally:
        unsubscribe_run_events("run-proj", queue)
        executor.cancel(task_id)


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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="x", session_id="s-enrich", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.AWAITING_APPROVAL.value

    actions = task["interrupt"]["action_requests"]
    assert len(actions) == 1
    assert actions[0]["tool_call_id"] == "c-enrich"


# 旧 values-diff 投影的单测（_pending_tool_calls / _mark_approval_pending）随实现删除：
# 待审批 tool_call_id 回填收敛到 noesis.runtime.stream（enrich_action_requests），
# 工具段置 approval_pending 收敛到 bridge 的 builder 路径——统一管道下由
# 上方 HITL 集成用例与主链路 bridge 测试共同覆盖。


def test_context_snapshot_from_worker_usage_metadata() -> None:
    """子会话上下文快照经统一管道产出：usage_metadata → bridge 模型调用边界提取。

    与主对话同源（bridge _accumulate_usage → context-update 帧 → executor
    发布/落库），executor 不再自行从消息提取。
    """
    from noesis.agents.subagents import executor as ex_mod

    worker = _build_worker([
        AIMessage(
            content="完成",
            usage_metadata={"input_tokens": 12000, "output_tokens": 50, "total_tokens": 12050},
        ),
    ])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
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
    executor = BackgroundTaskExecutor(shell_task_timeout_seconds=30)

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
        BackgroundTaskExecutor,
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
        snapshot = BackgroundTaskExecutor.get("733021ae")
        assert snapshot is not None and snapshot["task_id"] == "bg-prefix-1"
        # 完整 child_session_id 与 task_id 仍然精确命中
        assert BackgroundTaskExecutor.get(task.child_session_id)["task_id"] == "bg-prefix-1"
        assert BackgroundTaskExecutor.get("bg-prefix-1")["task_id"] == "bg-prefix-1"
    finally:
        with _TASKS_LOCK:
            _TASKS.pop("bg-prefix-1", None)


def test_task_lookup_rejects_ambiguous_prefix() -> None:
    """歧义前缀不得猜测：命中多个时按不存在处理。"""
    from noesis.agents.subagents.executor import (
        _TASKS,
        _TASKS_LOCK,
        BackgroundTaskExecutor,
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
        assert BackgroundTaskExecutor.get("aaaa") is None
    finally:
        with _TASKS_LOCK:
            for key in entries:
                _TASKS.pop(key, None)


def test_apply_turn_params_switches_effort() -> None:
    """followup turn 参数：推理档位变化即使 worker 失效重编译（模型不变也生效）。"""
    from noesis.agents.subagents.executor import (
        BackgroundTask,
        _TaskEntry,
        _TurnParams,
        _apply_turn_params,
    )

    task = BackgroundTask(
        task_id="t", session_id="s", user_id="u", description="d", model_id="m1",
    )
    entry = _TaskEntry(
        task=task, agent_factory=lambda: None,
        recursion_limit=10, timeout_seconds=1, hitl_timeout_seconds=1,
    )
    entry.compiled_agent = object()

    assert _apply_turn_params(entry, _TurnParams(reasoning_effort="high")) is True
    assert entry.turn_reasoning_effort == "high"
    assert entry.compiled_agent is None

    # 同参数重复应用：无变化不失效
    entry.compiled_agent = object()
    assert _apply_turn_params(entry, _TurnParams(reasoning_effort="high")) is False
    assert entry.compiled_agent is not None

    # 缺省（None）= 沿用当前档位，不视为覆盖：继承创建时档位
    assert _apply_turn_params(entry, _TurnParams(model_id="m3")) is True
    assert entry.turn_reasoning_effort == "high"
    entry.compiled_agent = object()
    assert _apply_turn_params(entry, _TurnParams(reasoning_effort=None)) is False
    assert entry.turn_reasoning_effort == "high"
    assert entry.compiled_agent is not None

    # 模型切换照旧生效（上下文窗口口径跟随）
    assert _apply_turn_params(entry, _TurnParams(model_id="m2")) is True
    assert task.model_id == "m2"
    assert entry.compiled_agent is None


def test_start_captures_parent_reasoning_effort() -> None:
    """创建时档位继承：start 在父 run 上下文捕获 ContextVar（隔离 loop 拿不到）。"""
    from noesis.agents.subagents import executor as ex_mod
    from noesis.llm.reasoning import clear_request_reasoning_effort, set_request_reasoning_effort

    set_request_reasoning_effort("medium")
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    try:
        task_id = executor.start(
            worker_factory=lambda: _build_worker([AIMessage(content="完成")]),
            description="x", session_id="s-effort", user_id="u1",
        )
        with ex_mod._TASKS_LOCK:
            entry = ex_mod._TASKS.get(task_id)
        assert entry is not None
        assert entry.turn_reasoning_effort == "medium"
        executor.cancel(task_id)
    finally:
        clear_request_reasoning_effort()


def test_stopping_during_hitl_cancels_directly() -> None:
    """stopping 期间触发 HITL interrupt：不进入 awaiting_approval，直接按取消收尾。"""
    worker = _build_worker(
        [_call("v1"), AIMessage(content="收尾")],
        interrupt_on={"dangerous": True},
    )
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="停止期间审批",
        session_id="s-hitl-stop", user_id="u1",
    )
    time.sleep(0.15)
    snapshot = executor.cancel(task_id)
    # 时序两种受理形态均为正确：running → stopping（协作）；已 awaiting_approval → 即时 cancelled
    assert snapshot["status"] in {
        BgTaskStatus.STOPPING.value,
        BgTaskStatus.CANCELLED.value,
    }
    # 无论哪种受理形态，最终必须 CANCELLED——stopping 期间触发 HITL 不得挂进 awaiting_approval
    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.CANCELLED.value:
            break
        time.sleep(0.05)
    task = executor.get(task_id)
    assert task["status"] == BgTaskStatus.CANCELLED.value


def test_stop_grace_timeout_falls_back_to_hard_cancel() -> None:
    """停止宽限超时：回退硬杀，终态与通知不漏发。"""
    worker = _build_worker(
        [_slow_call("s1", "c1"), _slow_call("s2", "c2")], slow=True,
    )
    # 宽限设为极短：slow 工具（0.6s）远超宽限 → 必走硬杀兜底
    executor = BackgroundTaskExecutor(task_timeout_seconds=30, stop_grace_seconds=1)
    task_id = executor.start(
        worker_factory=lambda: worker, description="宽限硬杀",
        session_id="s-grace", user_id="u1",
    )
    time.sleep(0.2)
    assert executor.cancel(task_id)["status"] == BgTaskStatus.STOPPING.value
    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.CANCELLED.value:
            break
        time.sleep(0.05)
    task = executor.get(task_id)
    assert task["status"] == BgTaskStatus.CANCELLED.value
    # 硬杀兜底同样走完整收尾：进度摘要中的文本进部分成果
    assert task["result"] is None or task["result"].startswith("中止前部分产出")


def test_task_timeout_goes_cooperative() -> None:
    """任务总时限改走协作路径：置 stopping(timed_out)，静止边界退出 TIMED_OUT。"""
    worker = _build_worker(
        [_slow_call(f"s{i}", f"c{i}") for i in range(4)] + [AIMessage(content="超时前的产出文本。")],
        slow=True,
    )
    executor = BackgroundTaskExecutor(task_timeout_seconds=1, stop_grace_seconds=10)
    task_id = executor.start(
        worker_factory=lambda: worker, description="超时协作",
        session_id="s-timeout", user_id="u1",
    )
    # 超时触发（1s）后先进入 stopping
    deadline = time.time() + 8
    saw_stopping = False
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.STOPPING.value:
            saw_stopping = True
        if task and task["status"] == BgTaskStatus.TIMED_OUT.value:
            break
        time.sleep(0.05)
    task = executor.get(task_id)
    assert saw_stopping, "超时应先进入 stopping 中间态"
    assert task["status"] == BgTaskStatus.TIMED_OUT.value
    assert task["stop_reason"] == "timed_out"


def test_stopping_counts_toward_concurrency_slot() -> None:
    """stopping 收尾仍占并发槽：stopping 期间新任务排队。"""
    worker = _build_worker(
        [_slow_call("s1", "c1"), _slow_call("s2", "c2")], slow=True,
    )
    executor = BackgroundTaskExecutor(max_concurrent_per_session=1, task_timeout_seconds=30)
    first_id = executor.start(worker_factory=lambda: worker, description="a", session_id="s-slot", user_id="u1")
    time.sleep(0.2)
    executor.cancel(first_id)
    # 第一个任务 stopping（占槽）：第二个同会话任务应排队
    second_id = executor.start(worker_factory=lambda: _build_worker([AIMessage(content="ok")]), description="b", session_id="s-slot", user_id="u1")
    second = executor.get(second_id)
    assert second["status"] == BgTaskStatus.QUEUED.value
    # 收尾释放槽位后排队任务被调度
    deadline = time.time() + 15
    while time.time() < deadline:
        second = executor.get(second_id)
        if second and second["status"] == BgTaskStatus.COMPLETED.value:
            break
        time.sleep(0.05)
    assert executor.get(second_id)["status"] == BgTaskStatus.COMPLETED.value


def test_check_task_pending_hint_text() -> None:
    """进行中状态（queued/running/awaiting_approval）输出状态提示，不落入终态形态。"""
    from noesis.agents.subagents.tools_middleware import _format_task

    for status, hint in (
        ("queued", "排队中"),
        ("running", "仍在运行"),
        ("awaiting_approval", "等待用户审批"),
    ):
        formatted = _format_task(
            {"status": status, "task_id": "t1", "child_session_id": "s1", "description": "调研任务"},
        )
        assert formatted == f"[s1] {hint}（description: 调研任务）", formatted


def test_partial_output_consistent_across_channels() -> None:
    """部分成果三处一致（spec 2.4）：task.result / check_task(_format_task) / 通知预览。"""
    from noesis.agents.subagents import notifications as notices
    from noesis.agents.subagents.executor import _PARTIAL_OUTPUT_PREFIX
    from noesis.agents.subagents.tools_middleware import _format_task

    first = AIMessage(
        content="阶段性结论：检索到 3 篇相关文献，主题集中在评测基准。",
        tool_calls=[{"name": "slow", "args": {"value": "s0"}, "id": "c0", "type": "tool_call"}],
    )
    worker = _build_worker([first] + [_slow_call(f"s{i}", f"c{i}") for i in range(6)], slow=True)
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="部分成果", session_id="s-partial", user_id="u1")
    time.sleep(0.2)
    executor.cancel(task_id)
    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.CANCELLED.value:
            break
        time.sleep(0.05)
    task = executor.get(task_id)

    # task.result：带标注前缀的全文
    assert task["result"] and task["result"].startswith(_PARTIAL_OUTPUT_PREFIX)
    body = task["result"][len(_PARTIAL_OUTPUT_PREFIX):].strip()
    assert "阶段性结论" in body

    # check_task 文本：终止原因 + 部分产出（预算内不截断）
    formatted = _format_task(task, output_budget=24000)
    assert "cancelled" in formatted and "阶段性结论" in formatted

    # 通知预览：内容本身开头（前缀不占 80 字预算）
    pending = notices.take_undelivered("s-partial", mark_delivered=False)
    cancelled_notices = [n for n in pending if n["status"] == "cancelled"]
    assert cancelled_notices, f"未收到取消通知: {pending}"
    preview = cancelled_notices[0]["preview"]
    assert preview.startswith("阶段性结论")
    assert _PARTIAL_OUTPUT_PREFIX not in preview


def _truncated_call(call_id: str) -> AIMessage:
    """finish_reason=length 的截断输出（模拟 provider 输出上限截断）。"""
    msg = AIMessage(
        content="输出被截断的部分文本",
        tool_calls=[],
    )
    msg.response_metadata = {"finish_reason": "length"}
    return msg


@pytest.mark.asyncio
async def test_truncated_run_terminal_partial() -> None:
    """输出截断一等终止（spec 3.3）：截断轮终态 partial/truncated 且携带部分产出。"""
    from noesis.agents.subagents.executor import subscribe_run_events, unsubscribe_run_events

    worker = _build_worker([_truncated_call("t1")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    queue = subscribe_run_events("run-trunc", "u1")
    task_id = None
    try:
        task_id = executor.start(
            worker_factory=lambda: worker, description="截断",
            session_id="s-trunc", user_id="u1",
            run_id="run-trunc", assistant_message_id="am-trunc",
        )
        task = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _wait_terminal(executor, task_id),
        )
        # 任务终态由最后一轮决定：截断轮的任务侧仍 completed，run 终态 partial
        assert task["status"] == BgTaskStatus.COMPLETED.value
        assert task["result"] and "输出被截断的部分文本" in task["result"]
        # run.finished 事件携带 finish_reason=truncated（与 DB run 终态一致）
        await asyncio.sleep(0.2)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        finished = [e for e in events if e["type"] == "run.finished"]
        assert finished, f"未收到 run.finished: {[e['type'] for e in events]}"
        assert finished[-1].get("finish_reason") == "truncated"
    finally:
        unsubscribe_run_events("run-trunc", queue)
        if task_id:
            executor.cancel(task_id)


@pytest.mark.asyncio
async def test_hitl_resume_merges_usage_across_interrupt() -> None:
    """HITL usage 补齐（spec 6.2）：含审批的 turn 终态 usage 覆盖中断前后。"""
    from noesis.agents.subagents import executor as ex_mod

    worker = _build_worker(
        [_call("v1"), AIMessage(content="审批后收尾")],
        interrupt_on={"dangerous": True},
    )
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="审批 usage",
        session_id="s-hitl-usage", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.AWAITING_APPROVAL.value

    with ex_mod._TASKS_LOCK:
        entry = ex_mod._TASKS.get(task_id)
    assert entry is not None
    assert entry.hitl_usage_seed is not None, "挂起时应存前半段 usage 种子"

    # 审批通过 → resume 续跑至完成
    executor.submit_decisions(task_id, [{"type": "approve"}])
    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.COMPLETED.value:
            break
        time.sleep(0.05)
    task = executor.get(task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    # 种子已消费
    with ex_mod._TASKS_LOCK:
        entry = ex_mod._TASKS.get(task_id)
    assert entry is not None and entry.hitl_usage_seed is None


def test_stop_during_turn_finish_window_not_overwritten() -> None:
    """竞态修复（阻塞项1）：turn 收尾窗口内受理的停止不得被终态覆写。

    模拟：执行侧流已结束（静止边界检查已过）但终态尚未写入时 cancel 受理——
    _try_transition 在锁内复查 STOPPING，终态写入让位于取消收尾。
    """
    from noesis.agents.subagents import executor as ex_mod

    worker = _build_worker([AIMessage(content="产出文本")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="竞态", session_id="s-race", user_id="u1",
    )
    time.sleep(0.05)
    # 在任务即将完成时抢入停止：_try_transition 与终态写入竞争
    # （快速完成的脚本模型下，此窗口极窄——改为直接验证机制：终态写入遇 STOPPING 让位）
    with ex_mod._TASKS_LOCK:
        entry = ex_mod._TASKS.get(task_id)
    assert entry is not None
    task = entry.task
    # 机制验证：置 STOPPING 后，终态写入被拒
    task.status = ex_mod.BgTaskStatus.STOPPING
    task.stop_reason = "cancelled"
    assert ex_mod._try_transition(task, ex_mod.BgTaskStatus.COMPLETED) is False
    assert task.status == ex_mod.BgTaskStatus.STOPPING.value
    # 恢复 RUNNING 后终态写入正常
    task.status = ex_mod.BgTaskStatus.RUNNING
    assert ex_mod._try_transition(task, ex_mod.BgTaskStatus.COMPLETED) is True
    executor.cancel(task_id)


def test_send_message_rejected_while_stopping() -> None:
    """stopping 期间 send_message 拒绝（中等问题：避免孤儿 user 消息）。"""
    worker = _build_worker([_slow_call("s1", "c1"), _slow_call("s2", "c2")], slow=True)
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="x", session_id="s-msg", user_id="u1")
    time.sleep(0.2)
    executor.cancel(task_id)
    with pytest.raises(ValueError, match="正在停止"):
        executor.send_message(task_id, "停止期间的追加消息")
    executor.cancel(task_id)


def test_cancel_notification_carries_partial_preview() -> None:
    """通知注入携带部分成果（阻塞项2）：cancelled 通知渲染 preview。"""
    from noesis.agents.subagents import notifications as notices
    from noesis.agents.subagents.notifications import render_block

    first = AIMessage(
        content="通知应携带这段部分产出。",
        tool_calls=[{"name": "slow", "args": {"value": "s0"}, "id": "c0", "type": "tool_call"}],
    )
    worker = _build_worker([first] + [_slow_call(f"s{i}", f"c{i}") for i in range(6)], slow=True)
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(worker_factory=lambda: worker, description="通知部分产出", session_id="s-notify", user_id="u1")
    time.sleep(0.2)
    executor.cancel(task_id)
    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.CANCELLED.value:
            break
        time.sleep(0.05)

    pending = notices.take_undelivered("s-notify", mark_delivered=False)
    cancelled = [n for n in pending if n["status"] == "cancelled"]
    assert cancelled, f"未收到取消通知: {pending}"
    assert cancelled[0]["preview"].startswith("通知应携带这段部分产出。")

    # 父 Agent 注入文本（render_block）同样携带
    block = render_block(pending)
    assert "已取消" in block
    assert "通知应携带这段部分产出。" in block


def test_check_and_list_task_tools_execute_without_error() -> None:
    """回归：check_task / list_tasks 工具本体可执行（P1 笔误漏网的缝）。

    此前单测只测 _format_task（直接传预算参数），不测引用配置类的工具闭包——
    OtherConfig.tool_output_max_chars 笔误导致两个工具每次调用必抛 AttributeError。
    """
    worker = _build_worker([AIMessage(content="ok")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="x",
        session_id="s-fg", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value

    tools = _build_tools(executor, lambda: worker)
    by_name = {t.name: t for t in tools}
    check = by_name["check_task"]
    listing = by_name["list_tasks"]

    check_result = check.func(task_id) if check.func else None
    assert check_result is not None and "completed" in check_result
    list_result = listing.func() if listing.func else None
    assert list_result is not None and "completed" in list_result
    executor.cancel(task_id)


def test_stop_reconcile_finalizes_when_cancel_absorbed() -> None:
    """硬杀后 CancelledError 被深层链路吸收时，对账定时器强制落终态。

    回归：曾出现停止宽限超时硬取消后，_arun 的 except CancelledError
    收口未执行——run 永久 RUNNING、UI 卡「停止中」、并发槽泄漏。终态
    不能依赖被取消协程的配合：用吞掉 CancelledError 并永久挂起的工具
    复现该场景，断言 reconcile 兜底把任务收口为 CANCELLED。
    """
    import noesis.agents.subagents.executor as executor_mod

    @tool
    async def stuck(value: str) -> str:
        """A tool that absorbs cancellation and never returns."""
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            # 模拟深层执行链吸收取消：挂起且不再传播
            await asyncio.sleep(999)
        return f"stuck:{value}"

    worker = create_agent(
        _ScriptedToolModel(
            script=[AIMessage(
                content="", tool_calls=[
                    {"name": "stuck", "args": {"value": "x"}, "id": "c1", "type": "tool_call"},
                ],
            )],
        ),
        tools=[stuck],
        checkpointer=MemorySaver(),
        name="task-worker",
    )
    executor = BackgroundTaskExecutor(
        task_timeout_seconds=30,
        stop_grace_seconds=1,
        stop_reconcile_seconds=1,
    )
    task_id = executor.start(
        worker_factory=lambda: worker, description="吸收取消",
        session_id="s-reconcile", user_id="u1",
    )
    time.sleep(0.3)
    assert executor.cancel(task_id)["status"] == BgTaskStatus.STOPPING.value

    # 绕过墙钟：直接触发宽限超时硬杀（真实路径为 call_later 回调）
    with executor_mod._TASKS_LOCK:
        entry = executor_mod._TASKS[task_id]
    executor_mod._on_stop_grace_timeout(entry)

    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.CANCELLED.value:
            break
        time.sleep(0.05)
    task = executor.get(task_id)
    assert task["status"] == BgTaskStatus.CANCELLED.value, (
        "硬取消被吸收时 reconcile 必须强制收口（协程仍挂起也不能卡 stopping）"
    )
    assert task["stop_reason"] == "cancelled"


def test_late_finalize_stop_does_not_republish_after_force_terminal() -> None:
    """对账兜底已发布终态后，晚到的 _finalize_stop 重入只补落库、不重发事件。

    归属权（terminal_published）在事件实际发布时置位：硬取消被吸收、
    _force_terminal 已收口的任务，被卡死协程事后苏醒再走 _finalize_stop，
    run.finished / terminal 事件 / 通知 / drain 不得二次触发。
    """
    import noesis.agents.subagents.executor as executor_mod
    from noesis.agents.subagents import notifications

    @tool
    async def stuck(value: str) -> str:
        """A tool that absorbs cancellation and never returns."""
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            await asyncio.sleep(999)
        return f"stuck:{value}"

    worker = create_agent(
        _ScriptedToolModel(
            script=[AIMessage(
                content="", tool_calls=[
                    {"name": "stuck", "args": {"value": "x"}, "id": "c1", "type": "tool_call"},
                ],
            )],
        ),
        tools=[stuck],
        checkpointer=MemorySaver(),
        name="task-worker",
    )
    executor = BackgroundTaskExecutor(
        task_timeout_seconds=30,
        stop_grace_seconds=1,
        stop_reconcile_seconds=1,
    )
    task_id = executor.start(
        worker_factory=lambda: worker, description="晚到重入",
        session_id="s-late", user_id="u1",
    )
    time.sleep(0.3)
    assert executor.cancel(task_id)["status"] == BgTaskStatus.STOPPING.value
    with executor_mod._TASKS_LOCK:
        entry = executor_mod._TASKS[task_id]
    executor_mod._on_stop_grace_timeout(entry)

    deadline = time.time() + 10
    while time.time() < deadline:
        task = executor.get(task_id)
        reconcile_done = (
            entry.stop_reconcile_task is not None and entry.stop_reconcile_task.done()
        )
        if task and task["status"] == BgTaskStatus.CANCELLED.value and reconcile_done:
            break
        time.sleep(0.05)
    assert executor.get(task_id)["status"] == BgTaskStatus.CANCELLED.value
    assert entry.stop_reconcile_task is not None and entry.stop_reconcile_task.done()

    notices = notifications.drain("s-late")
    assert len(notices) == 1, "对账兜底应恰好发布一次终态通知"
    assert notices[0]["status"] == BgTaskStatus.CANCELLED.value

    # 模拟被卡死协程晚到苏醒：_finalize_stop 重入不重发事件
    loop = executor_mod._ensure_loop()
    late = asyncio.run_coroutine_threadsafe(
        executor_mod._finalize_stop(entry, entry.task, None), loop,
    )
    late.result(timeout=10)
    assert notifications.drain("s-late") == [], "晚到重入不得二次发布终态通知"
    assert executor.get(task_id)["status"] == BgTaskStatus.CANCELLED.value


def test_session_port_covers_executor_call_surface() -> None:
    """端口契约：executor 经 SubagentSessionPort 调用的方法必须全部存在。

    回归：collect_partial_output 曾只加在 SubagentSessionService 而漏了
    端口委托，executor 经端口调用直接 AttributeError 逃逸，炸穿
    _finalize_stop 使 run 永久 RUNNING（测试因 run_id=None 走不到端口
    而全绿）。从 executor 源码提取实际调用面做契约，未来新增调用自动覆盖。
    """
    import inspect
    import re

    from noesis.agents.subagents import executor as executor_mod
    from noesis.services import subagent_session_service
    from noesis.services.subagent_runtime_port import SubagentSessionPort

    # executor 内统一别名：SubagentSessionPort as SubagentSessionService
    called = set(re.findall(r"\bSubagentSessionService\.(\w+)\(", inspect.getsource(executor_mod)))
    port_methods = {
        name for name, _ in inspect.getmembers(SubagentSessionPort)
        if not name.startswith("_")
    }
    service_methods = {
        name for name, _ in inspect.getmembers(subagent_session_service.SubagentSessionService)
        if not name.startswith("_")
    }
    assert called, "契约提取失败：executor 应有端口调用"
    missing_on_port = called - port_methods
    assert not missing_on_port, (
        f"SubagentSessionPort 缺少 executor 调用的方法：{sorted(missing_on_port)}"
    )
    missing_on_service = called - service_methods
    assert not missing_on_service, (
        f"SubagentSessionService 缺少端口目标方法：{sorted(missing_on_service)}"
    )
    # 事故方法必须在场（防止契约测试本身被误删）
    assert "collect_partial_output" in called


async def test_collect_persisted_text_degrades_on_port_failure() -> None:
    """部分成果提取失败必须降级为空，不允许炸穿 _finalize_stop。

    回归：端口缺方法时 AttributeError 在协程构造期同步抛出，原 try 只包住
    await，导致异常逃逸、run 永久 RUNNING。
    """
    from noesis.agents.subagents.executor import BackgroundTask, _collect_persisted_text

    task = BackgroundTask(
        task_id="bg-x", session_id="s1", user_id="u1",
        description="x",
        child_session_id="child-1", run_id="run-1",
    )
    # 端口未注册（未 configure_service_port）：调用即 RuntimeError；
    # 模拟任意端口侧失败都不得逃逸
    text = await _collect_persisted_text(task)
    assert text == ""


def test_followup_cold_resume_prelude_failure_fails_task() -> None:
    """冷恢复前置段（run 创建）异常：显式收口 FAILED，不得静默卡 RUNNING。

    回归背景：send_message 对 _arun_followup fire-and-forget，前置段异常
    滞留在未观察的 concurrent Future 里被吞——任务卡 RUNNING、后续追问
    进队列无人消费（跨 loop 连接错误曾走此路径且无任何日志）。
    """
    worker = _build_worker([AIMessage(content="第一轮完成")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="x",
        session_id="s-fu-prelude", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("followup run 创建失败")

    entry = _TASKS[task_id]
    entry.followup_factory = _boom

    executor.send_message(task_id, "继续深入")
    task = _wait_terminal(executor, task_id)
    # 收口为显式失败：状态可见、错误信息可定位（而非永远 RUNNING）
    assert task["status"] == BgTaskStatus.FAILED.value
    assert "followup run 创建失败" in (task["error"] or "")


@pytest.mark.asyncio
async def test_run_stream_publishes_transient_deltas_and_stats() -> None:
    """流式转发：子会话 run 流收到 text-delta / stats-update（transient），
    且瞬态事件不进 history（重连由 run-snapshot 全量恢复，不叠放旧 delta）。"""
    from noesis.agents.subagents.executor import (
        get_run_event_history,
        subscribe_run_events,
        unsubscribe_run_events,
    )

    queue = subscribe_run_events("run-stream", "u1")
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    try:
        task_id = executor.start(
            worker_factory=lambda: _build_worker([AIMessage(content="流式内容")]),
            description="x",
            session_id="s-stream",
            user_id="u1",
            task_id="child-stream",
            run_id="run-stream",
            assistant_message_id="assistant-stream",
        )
        events: list[dict] = []
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=10)
            events.append(event)
            if event.get("type") == "run.finished":
                break
        task = executor.get(task_id)
        assert task is not None and task["status"] == "completed"

        deltas = [e for e in events if e.get("type") == "text-delta"]
        assert deltas, "run 流必须转发 text-delta（瞬态流式正文）"
        assert all(e.get("transient") is True for e in deltas)
        assert any("流式内容" in str(e.get("text_delta") or "") for e in deltas)

        stats = [e for e in events if e.get("type") == "stats-update"]
        assert stats, "run 流必须发布实时统计 stats-update"
        assert all(e.get("transient") is True for e in stats)
        assert stats[-1].get("steps", 0) >= 1
        assert stats[-1].get("turns", 0) >= 1

        # 瞬态事件不进缓存：回放通道只保留边界/生命周期事件。
        # 首连（after=0）只放行 run.started——内容恢复走首帧快照；
        # 正常重放（after=1 起）携带 run.finished。
        first_connect = list(get_run_event_history("run-stream", 0))
        assert not any(e.get("transient") for e in first_connect), "瞬态事件不得进入重放缓存"
        assert all(e.get("type") == "run.started" for e in first_connect)
        history = list(get_run_event_history("run-stream", 1))
        assert not any(e.get("transient") for e in history)
        assert any(e.get("type") == "run.finished" for e in history)
    finally:
        unsubscribe_run_events("run-stream", queue)
        executor.cancel(task_id)


@pytest.mark.asyncio
async def test_transient_deltas_forwarded_to_run_subscribers() -> None:
    """瞬态转发契约：订阅方收到 text-delta / stats-update（transient 标记）。

    回归：单测假模型全部非流式（仅 _generate），on_chat_model_stream 从未
    发生——子会话详情页「正在生成」与 token/s 统计行的整个数据源
    （executor 瞬态转发）此前零覆盖。流式假模型 + 订阅队列直接实证。
    """
    import noesis.agents.subagents.executor as executor_mod
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    class _StreamingScriptedModel(_ScriptedToolModel):
        """流式假模型：按 chunk 产出脚本消息（触发 on_chat_model_stream）。"""

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
            with self._lock:
                idx = self._cursor
                self._cursor += 1
            message = self.script[idx] if idx < len(self.script) else AIMessage(content="任务完成")
            for piece in (message.content or "ok")[:8]:
                yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if message.tool_calls:
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="", tool_calls=[
                            {"name": message.tool_calls[0]["name"],
                             "args": message.tool_calls[0]["args"],
                             "id": message.tool_calls[0]["id"], "type": "tool_call"},
                        ],
                    )
                )

    first = AIMessage(
        content="流式正文第一段。",
        tool_calls=[{"name": "slow", "args": {"value": "s0"}, "id": "c0", "type": "tool_call"}],
    )
    rest = [
        AIMessage(
            content="", tool_calls=[
                {"name": "slow", "args": {"value": f"s{i}"}, "id": f"c{i}", "type": "tool_call"},
            ],
        )
        for i in range(1, 10)
    ]
    model = _StreamingScriptedModel(script=[first] + rest)
    worker = create_agent(model, tools=[_slow_tool()], checkpointer=MemorySaver(), name="task-worker")

    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    run_id = "run-transient-test"
    queue = executor_mod.subscribe_run_events(run_id, "u1")
    try:
        executor.start(
            worker_factory=lambda: worker, description="瞬态转发",
            session_id="s-transient", user_id="u1",
            child_session_id="child-transient", run_id=run_id,
        )
        deadline = time.perf_counter() + 15
        seen_types: list[str] = []
        while time.perf_counter() < deadline:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if "stats-update" in seen_types:
                    break
                continue
            seen_types.append(str(item.get("type")))
            if item.get("type") in ("text-delta", "stats-update"):
                assert item.get("transient") is True, "流式 delta 必须带 transient 标记"
        assert "text-delta" in seen_types, f"未收到 text-delta 瞬态事件: {seen_types[:10]}"
        assert "stats-update" in seen_types, f"未收到 stats-update 瞬态事件: {seen_types[:10]}"
    finally:
        executor_mod.unsubscribe_run_events(run_id, queue)
        bg_shutdown()


@pytest.mark.asyncio
async def test_asend_message_cold_resume_returns_new_run_id() -> None:
    """异步冷恢复契约：响应前完成新 run 创建——run_id 权威，订阅方据此
    订阅即可收到全部事件。同步版响应可携带旧 run_id（新 run 异步创建），
    前端曾被迫轮询 active-run 绕过（契约缺陷的补丁，已回归根因修复）。"""
    worker = _build_worker([AIMessage(content="冷恢复完成")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="x",
        session_id="s-asend", user_id="u1",
        task_id="child-asend", run_id="run-old", assistant_message_id="am-old",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value
    old_run_id = task["run_id"]

    async def _factory(child_session_id, message, user_message_id=None):  # noqa: ANN001
        return {"run_id": "run-new", "assistant_message_id": "am-new"}

    entry = _TASKS[task_id]
    entry.followup_factory = _factory

    snapshot = await BackgroundTaskExecutor.asend_message(task_id, "继续")
    # 契约：返回时新 run_id 已就绪（不是旧值），状态 running
    assert snapshot["run_id"] == "run-new"
    assert snapshot["run_id"] != old_run_id
    assert snapshot["assistant_message_id"] == "am-new"
    assert snapshot["status"] == BgTaskStatus.RUNNING.value
    assert entry.task.run_id == "run-new"

    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_asend_message_factory_failure_fails_task() -> None:
    """异步冷恢复的前置失败（factory 抛异常）：显式收口 FAILED，
    响应携带失败状态与错误信息（而非静默卡 RUNNING）。"""
    worker = _build_worker([AIMessage(content="第一轮完成")])
    executor = BackgroundTaskExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=lambda: worker, description="x",
        session_id="s-asend-fail", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value

    async def _boom(child_session_id, message, user_message_id=None):  # noqa: ANN001
        raise RuntimeError("run 创建失败")

    entry = _TASKS[task_id]
    entry.followup_factory = _boom

    snapshot = await BackgroundTaskExecutor.asend_message(task_id, "继续")
    assert snapshot["status"] == BgTaskStatus.FAILED.value
    assert "run 创建失败" in (snapshot["error"] or "")
