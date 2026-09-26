"""bg-task-durable-facts 单测（无 DB）：状态映射、终态回收、查询回退、
pending 翻转、队列重建、落库重试耗尽。

DB 相关路径（pending 行 SQL、投影 SQL、shell 行）标 integration，由
tests/api 集成轮覆盖；本文件用端口假体验证行为契约。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from noesis.agents.background.executor import BackgroundTaskExecutor, _drain_restored
from noesis.agents.background.jobs.registry import (
    _PENDING_QUEUES,
    _TASKS,
    _TASKS_LOCK,
    _TaskEntry,
    configure_terminal_reclaim,
    reclaim_terminal_entries,
)
from noesis.agents.background.jobs.settle import _persist_terminal_with_retry
from noesis.agents.background.jobs.state import (
    BackgroundTask,
    BgTaskStatus,
    run_status_to_task_status,
)


# ---------------------------------------------------------------------------
# 状态映射（内存快照与 DB 投影共用的唯一函数）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("run_status", "finish_reason", "expected"),
    [
        ("queued", None, "queued"),
        ("running", None, "running"),
        ("retrying", None, "running"),
        ("hitl_pending", None, "running"),
        ("completed", "stop", "completed"),
        ("error", "error", "failed"),
        ("partial", "cancelled", "cancelled"),
        ("partial", "timeout", "timed_out"),
        # 截断沿用现行终态处理规则：任务级已完成（不制造续聊资格悬崖）
        ("partial", "truncated", "completed"),
        ("partial", "stopped", "cancelled"),
        ("partial", None, "cancelled"),
        ("interrupted", "stopped", "cancelled"),
    ],
)
def test_run_status_mapping_full_domain(
    run_status: str, finish_reason: str | None, expected: str,
) -> None:
    assert run_status_to_task_status(run_status, finish_reason) == expected


# ---------------------------------------------------------------------------
# 终态回收：资格三件套 + retention + 上限保序
# ---------------------------------------------------------------------------

def _make_terminal_entry(
    task_id: str,
    *,
    completed_at: float,
    published: bool = True,
    persist_ok: bool = True,
    exhausted: bool = False,
    notified: bool = True,
    status: BgTaskStatus = BgTaskStatus.COMPLETED,
) -> _TaskEntry:
    task = BackgroundTask(
        task_id=task_id,
        session_id="s-rec",
        user_id="u1",
        description="x",
        status=status,
        completed_at=completed_at,
    )
    entry = _TaskEntry(
        task=task, agent_factory=None, recursion_limit=10, timeout_seconds=0,
    )
    entry.terminal_published = published
    entry.terminal_persist_ok = persist_ok
    entry.terminal_persist_exhausted = exhausted
    entry.terminal_notified = notified
    return entry


@pytest.fixture()
def _reclaim_env():
    """小旋钮 + 前后清空全局注册表（隔离其他用例遗留的终态条目）。"""
    with _TASKS_LOCK:
        _TASKS.clear()
        _PENDING_QUEUES.clear()
    configure_terminal_reclaim(retention_seconds=50.0, reclaim_max=2)
    yield
    with _TASKS_LOCK:
        _TASKS.clear()
        _PENDING_QUEUES.clear()
    configure_terminal_reclaim(retention_seconds=3600.0, reclaim_max=200)


def test_reclaim_respects_qualification(_reclaim_env) -> None:
    """收尾在途（未发布/落库未确认/未通知）的条目 SHALL NOT 被回收。"""
    now = time.time()
    with _TASKS_LOCK:
        _TASKS["bg-old-ok"] = _make_terminal_entry("bg-old-ok", completed_at=now - 100)
        # 未发布：retention 已过也不得回收
        _TASKS["bg-old-unpublished"] = _make_terminal_entry(
            "bg-old-unpublished", completed_at=now - 100, published=False,
        )
        # 落库未确认（无耗尽标记）：不得回收
        _TASKS["bg-old-unpersisted"] = _make_terminal_entry(
            "bg-old-unpersisted", completed_at=now - 100, persist_ok=False,
        )
        # 通知未记录：不得回收
        _TASKS["bg-old-unnotified"] = _make_terminal_entry(
            "bg-old-unnotified", completed_at=now - 100, notified=False,
        )
    evicted = reclaim_terminal_entries()
    assert evicted == 1
    with _TASKS_LOCK:
        assert "bg-old-ok" not in _TASKS
        for task_id in ("bg-old-unpublished", "bg-old-unpersisted", "bg-old-unnotified"):
            assert task_id in _TASKS


def test_reclaim_cap_evicts_oldest_first(_reclaim_env) -> None:
    """终态条目超上限：按终态时间从最旧回收，活跃任务不受影响。"""
    now = time.time()
    with _TASKS_LOCK:
        _TASKS["bg-t1"] = _make_terminal_entry("bg-t1", completed_at=now - 30)
        _TASKS["bg-t2"] = _make_terminal_entry("bg-t2", completed_at=now - 20)
        _TASKS["bg-t3"] = _make_terminal_entry("bg-t3", completed_at=now - 10)
        _TASKS["bg-active"] = _make_terminal_entry(
            "bg-active", completed_at=None, published=False,
            status=BgTaskStatus.RUNNING,
        )
        _TASKS["bg-active"].task.status = BgTaskStatus.RUNNING
        _TASKS["bg-active"].task.completed_at = None
    evicted = reclaim_terminal_entries()
    assert evicted == 1
    with _TASKS_LOCK:
        assert "bg-t1" not in _TASKS  # 最旧先回收
        assert "bg-t2" in _TASKS and "bg-t3" in _TASKS


def test_reclaim_persist_exhausted_is_eligible(_reclaim_env) -> None:
    """落库重试耗尽的条目允许回收（有界内存目标优先于滞留）。"""
    now = time.time()
    with _TASKS_LOCK:
        _TASKS["bg-exhausted"] = _make_terminal_entry(
            "bg-exhausted", completed_at=now - 100, persist_ok=False, exhausted=True,
        )
    assert reclaim_terminal_entries() == 1
    with _TASKS_LOCK:
        assert "bg-exhausted" not in _TASKS


# ---------------------------------------------------------------------------
# 查询回退与 pending 翻转（端口假体）
# ---------------------------------------------------------------------------

class _FakeSessionService:
    def __init__(self) -> None:
        self.pending: dict[str, list[dict]] = {}
        self.dropped: list[str] = []
        self.cold_tasks: dict[str, dict] = {}
        self.exhausted_marks: list[str] = []

    async def count_pending_messages(self, session_id: str) -> int:
        return len([r for r in self.pending.get(session_id, []) if not r.get("dropped")])

    async def create_pending_message(self, *, session_id: str, user_id: str, message: str,
                                     model_id=None, reasoning_effort=None) -> str:
        mid = f"pm-{len(self.pending.get(session_id, [])) + 1}"
        self.pending.setdefault(session_id, []).append({
            "message_id": mid, "text": message,
            "model_id": model_id, "reasoning_effort": reasoning_effort,
        })
        return mid

    async def flip_pending_message_dropped(self, message_id: str) -> int:
        for rows in self.pending.values():
            for row in rows:
                if row["message_id"] == message_id and not row.get("dropped"):
                    row["dropped"] = True
                    self.dropped.append(message_id)
                    return 1
        return 0

    async def flip_pending_messages_dropped(self, session_id: str) -> int:
        rows = [r for r in self.pending.get(session_id, []) if not r.get("dropped")]
        for row in rows:
            row["dropped"] = True
            self.dropped.append(row["message_id"])
        return len(rows)

    async def db_task_projection(self, task_ref: str):
        return self.cold_tasks.get(task_ref)

    async def list_db_task_projections(self, session_id: str):
        return []

    async def load_cold_task(self, task_ref: str):
        return self.cold_tasks.get(task_ref)

    async def list_queued_subagent_runs(self, db=None):
        return []

    async def mark_terminal_persist_exhausted(self, run_id: str) -> None:
        self.exhausted_marks.append(run_id)


class _FakeShellJobService:
    """shell 事实行假体（真实实现走 bg_shell_job 表）。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def persist_start(self, **kwargs) -> None:
        self.rows[kwargs["task_id"]] = {
            "task_id": kwargs["task_id"], "session_id": kwargs["session_id"],
            "user_id": kwargs.get("user_id"), "command": kwargs["command"],
            "kind": "shell", "subagent_type": None, "status": kwargs["status"],
            "result": None, "error": None,
            "started_at": None, "completed_at": None, "progress_count": 0,
            "undelivered_messages": 0,
        }

    async def mark_started(self, task_id: str) -> None:
        if task_id in self.rows:
            self.rows[task_id]["status"] = "running"

    async def mark_terminal(self, **kwargs) -> None:
        row = self.rows.get(kwargs["task_id"])
        if row is not None and row["status"] in ("queued", "running"):
            row["status"] = kwargs["status"]
            row["error"] = kwargs.get("error")
            row["result"] = kwargs.get("result_tail")

    async def get_task(self, task_id: str):
        return self.rows.get(task_id)

    async def list_for_session(self, session_id: str):
        return [r for r in self.rows.values() if r["session_id"] == session_id]

    async def reconcile_orphaned(self, db=None) -> int:
        return 0


@pytest.fixture()
def fake_session_port():
    from noesis.agents.background.ports import configure_service_port, configure_shell_job_port

    fake = _FakeSessionService()
    configure_service_port(fake)
    configure_shell_job_port(_FakeShellJobService())
    yield fake
    # 收尾不还原：后续用例自行装配（端口为模块级单例，注册即覆盖）


def test_cancel_with_fallback_idempotent_on_db_terminal(fake_session_port) -> None:
    """回收后的已取消任务再次取消：DB 终态幂等返回，SHALL NOT 报不存在。"""
    fake_session_port.cold_tasks["bg-evicted"] = {
        "task_id": "bg-evicted", "session_id": "s1", "child_session_id": "bg-evicted",
        "user_id": "u1", "description": "x", "kind": "subagent", "status": "cancelled",
    }
    executor = BackgroundTaskExecutor()
    snapshot = asyncio.run(executor.cancel_with_fallback("bg-evicted"))
    assert snapshot["status"] == "cancelled"


def test_evicted_shell_task_check_cancel_list_fallback(fake_session_port) -> None:
    """shell 任务回收后：check 返回事实行投影、重复取消幂等、list 含 DB 行
    （两 kind 兜底——subagent 投影 miss 后走 ShellJobPort）。"""
    from noesis.agents.background.ports import configure_shell_job_port

    shell_fake = _FakeShellJobService()
    shell_fake.rows["bg-shell-evicted"] = {
        "task_id": "bg-shell-evicted", "session_id": "s1", "user_id": "u1",
        "command": "echo done", "kind": "shell", "subagent_type": None,
        "status": "completed", "result": "exit code: 0", "error": None,
        "started_at": None, "completed_at": None, "progress_count": 0,
        "undelivered_messages": 0,
    }
    configure_shell_job_port(shell_fake)
    executor = BackgroundTaskExecutor()
    checked = asyncio.run(executor.check_with_fallback("bg-shell-evicted"))
    assert checked is not None and checked["status"] == "completed"
    snapshot = asyncio.run(executor.cancel_with_fallback("bg-shell-evicted"))
    assert snapshot["status"] == "completed"
    listing = asyncio.run(executor.list_with_fallback("s1"))
    assert any(t["task_id"] == "bg-shell-evicted" for t in listing)


def test_cancel_with_fallback_raises_when_no_db_fact(fake_session_port) -> None:
    executor = BackgroundTaskExecutor()
    with pytest.raises(ValueError):
        asyncio.run(executor.cancel_with_fallback("bg-unknown"))


def test_write_ahead_creates_pending_row_before_enqueue(fake_session_port) -> None:
    """write-ahead：工具路径（无 user_message_id）先落 pending 行再入镜像队列，
    turn 参数随行持久化。直接构造 RUNNING 条目（不启动执行机器）。"""
    from noesis.agents.background.jobs.registry import _TASKS, _TASKS_LOCK

    task = BackgroundTask(
        task_id="bg-wa", session_id="s-wa", user_id="u1", description="x",
        child_session_id="child-wa", status=BgTaskStatus.RUNNING,
    )
    entry = _TaskEntry(
        task=task, agent_factory=None, recursion_limit=10, timeout_seconds=30,
    )
    with _TASKS_LOCK:
        _TASKS[task.task_id] = entry
    try:
        executor = BackgroundTaskExecutor(task_timeout_seconds=30)
        mid = asyncio.run(fake_session_port.create_pending_message(
            session_id="child-wa", user_id="u1", message="聚焦中文源",
            model_id="model-b", reasoning_effort="high",
        ))
        snapshot = asyncio.run(executor.deliver_message(
            "bg-wa", "聚焦中文源", user_message_id=mid,
            model_id="model-b", reasoning_effort="high",
        ))
        assert snapshot["status"] == "running"
        with _TASKS_LOCK:
            queued = _TASKS["bg-wa"]
            assert len(queued.pending_messages) == 1
            assert queued.pending_messages[0].text == "聚焦中文源"
            assert queued.pending_messages[0].message_id == mid
        rows = fake_session_port.pending["child-wa"]
        assert len(rows) == 1
        assert rows[0]["model_id"] == "model-b"
        assert rows[0]["reasoning_effort"] == "high"
    finally:
        with _TASKS_LOCK:
            _TASKS.pop("bg-wa", None)


def test_restore_queued_rebuilds_from_db(fake_session_port, monkeypatch) -> None:
    """对账重建：queued child run 经 DB 事实重建条目并入排队队列。"""
    fake_session_port.cold_tasks["child-q1"] = {
        "task_id": "bg-q1", "session_id": "parent-1", "child_session_id": "child-q1",
        "user_id": "u1", "description": "排队任务", "kind": "subagent",
        "status": "queued", "subagent_type": "general", "model": None,
        "created_at": 1,
    }
    monkeypatch.setattr(
        "noesis.agents.background.executor._drain_restored", lambda: None,
    )
    executor = BackgroundTaskExecutor(
        task_timeout_seconds=30,
        cold_resolver=lambda subagent_type, model: (lambda: None),
    )
    restored = asyncio.run(executor.restore_queued([
        {"child_session_id": "child-q1", "created_at": 1},
    ]))
    assert restored == 1
    with _TASKS_LOCK:
        entry = _TASKS["bg-q1"]
        assert entry.task.status == BgTaskStatus.QUEUED
        assert any(
            e.task.task_id == "bg-q1"
            for queue in _PENDING_QUEUES.values() for e in queue
        )


def test_restore_queued_rejects_status_mismatch(fake_session_port) -> None:
    fake_session_port.cold_tasks["child-q2"] = {
        "task_id": "bg-q2", "session_id": "p", "child_session_id": "child-q2",
        "user_id": "u1", "description": "x", "kind": "subagent",
        "status": "completed", "subagent_type": "general", "model": None,
    }
    executor = BackgroundTaskExecutor(
        task_timeout_seconds=30,
        cold_resolver=lambda subagent_type, model: (lambda: None),
    )
    restored = asyncio.run(executor.restore_queued([
        {"child_session_id": "child-q2", "created_at": 1},
    ]))
    assert restored == 0


# ---------------------------------------------------------------------------
# 终态落库重试耗尽 → 诊断位 + 允许回收
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_retry_exhaustion_marks_diagnostic(monkeypatch) -> None:
    """终态落库有界重试：耗尽置 exhausted 旗标 + run 行诊断位（不伪造终态）。"""
    from noesis.agents.background.ports import SubagentSessionPort
    from noesis.config.env import StreamConfig
    from noesis.agents.background.jobs.settle import TaskTerminal
    from noesis.chat.runs import RunStatus

    entry = _make_terminal_entry(
        "bg-persist", completed_at=time.time(), persist_ok=False,
    )
    entry.task.run_id = "run-persist"
    entry.terminal_published = True

    async def _always_fail(task, terminal):
        raise RuntimeError("db down")

    async def _fake_mark(run_id: str) -> None:
        marks.append(run_id)

    marks: list[str] = []
    scheduled: list[Any] = []

    def _fake_run_on_main_loop(coro, name=None):
        scheduled.append(coro)
        return None

    monkeypatch.setattr(
        "noesis.runtime.main_loop.run_on_main_loop", _fake_run_on_main_loop,
    )
    monkeypatch.setattr(
        "noesis.agents.background.jobs.settle._persist_run_terminal", _always_fail,
    )
    # StreamConfig 为冻结 dataclass：object.__setattr__ 打补丁，测后还原
    original_timeout = StreamConfig.persistence_timeout_seconds
    object.__setattr__(StreamConfig, "persistence_timeout_seconds", 0.05)
    monkeypatch.setattr(
        SubagentSessionPort, "mark_terminal_persist_exhausted", _fake_mark,
    )
    terminal = TaskTerminal(
        task_status=BgTaskStatus.COMPLETED,
        run_status=RunStatus.COMPLETED,
        finish_reason="stop",
    )
    try:
        await _persist_terminal_with_retry(entry, entry.task, terminal, None)
    finally:
        object.__setattr__(
            StreamConfig, "persistence_timeout_seconds", original_timeout,
        )
    assert entry.terminal_persist_exhausted is True
    assert entry.terminal_persist_ok is False
    # 诊断位经 run_on_main_loop 调度（main loop 不可用时入 scheduled）
    assert len(scheduled) == 1
    for coro in scheduled:
        await coro
    assert marks == ["run-persist"]
