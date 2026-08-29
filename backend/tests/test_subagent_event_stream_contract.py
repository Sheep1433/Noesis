"""子 Agent 会话化（spec §9.9）回归：事件流契约与接口级用例。

覆盖界面难以手工验证的行为：
- 关闭抽屉 → SSE generator 被取消 → finally 退订（订阅注册表归零）
- 断线重连 → 按 snapshot.sequence 重放，不重不漏，run.finished 后终止
- 活跃流中低于游标的迟到事件被跳过
- 终态 run 不占用订阅
- 同名并行 launch 产生独立 child session / run
- 父会话软删级联到 child session 与运行中任务
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from server.api import chat_api


def _parse_frame(frame: str) -> tuple[str, Any]:
    """SSE 文本帧 → (event 名, data 对象)；[DONE] 帧返回 ("done", None)。"""
    event, data = "message", None
    for line in frame.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            payload = line[len("data:"):].strip()
            data = json.loads(payload) if payload != "[DONE]" else None
            if payload == "[DONE]":
                event = "done"
    return event, data


def _fake_snapshot(*, sequence: int, terminal: bool) -> SimpleNamespace:
    return SimpleNamespace(
        origin="subagent",
        is_terminal=terminal,
        sequence=sequence,
        to_dict=lambda: {"run_id": "r-1", "sequence": sequence},
    )


@pytest.fixture()
def subagent_streams(monkeypatch):
    """替换 SubagentSessionService 的执行器桥接，记录订阅/退订/历史调用。"""
    from noesis.services.subagent_session_service import SubagentSessionService

    import asyncio

    calls = {"subscribed": [], "unsubscribed": [], "history": []}

    def _subscribe(run_id: str, user_id: str):
        queue = asyncio.Queue()
        calls["subscribed"].append((run_id, user_id, queue))
        return queue

    def _unsubscribe(run_id: str, queue) -> None:
        calls["unsubscribed"].append((run_id, queue))

    def _history(run_id: str, after_sequence: int = 0):
        calls["history"].append((run_id, after_sequence))
        return list(calls.get("_history_items", []))

    monkeypatch.setattr(SubagentSessionService, "subscribe_run_events", _subscribe)
    monkeypatch.setattr(SubagentSessionService, "unsubscribe_run_events", _unsubscribe)
    monkeypatch.setattr(SubagentSessionService, "get_run_event_history", _history)
    return calls


async def _open_subagent_stream(monkeypatch, snapshot, after_sequence: int = 0):
    """驱动 stream_run 的 subagent 分支，返回 StreamingResponse。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _null_sse_db():
        yield SimpleNamespace()

    monkeypatch.setattr(
        chat_api.RunService, "get", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(chat_api, "sse_prefetch_db", _null_sse_db)
    return await chat_api.stream_run(
        "r-1",
        after_sequence=after_sequence,
        current_user=SimpleNamespace(user_id="u1"),
    )


# ---------------------------------------------------------------------------
# 关闭退订 / 断线重放（事件流契约）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_close_unsubscribes_run_events(monkeypatch, subagent_streams) -> None:
    """关闭抽屉 = 客户端 abort → generator 被关闭 → finally 退订，无泄漏。"""
    snapshot = _fake_snapshot(sequence=0, terminal=False)
    response = await _open_subagent_stream(monkeypatch, snapshot)

    stream = response.body_iterator
    event, _ = _parse_frame(await stream.__anext__())
    assert event == "run-snapshot"
    assert len(subagent_streams["subscribed"]) == 1

    # 等价于前端 AbortController.abort()
    await stream.aclose()

    assert len(subagent_streams["unsubscribed"]) == 1
    run_id, queue = subagent_streams["unsubscribed"][0]
    assert run_id == "r-1"
    assert queue is subagent_streams["subscribed"][0][2]


@pytest.mark.asyncio
async def test_reconnect_replays_after_snapshot_sequence(monkeypatch, subagent_streams) -> None:
    """断线重连：重放从权威快照序号之后开始，不重不漏，run.finished 后终止。"""
    subagent_streams["_history_items"] = [
        {"type": "message.updated", "sequence": 4},
        {"type": "message.updated", "sequence": 5},
        {"type": "run.finished", "sequence": 6},
    ]
    snapshot = _fake_snapshot(sequence=3, terminal=False)
    response = await _open_subagent_stream(monkeypatch, snapshot, after_sequence=3)

    frames = [_parse_frame(f) async for f in response.body_iterator]
    events = [e for e, _ in frames]
    sequences = [d.get("sequence") for _, d in frames[1:] if d is not None and "sequence" in d]

    assert events[0] == "run-snapshot"
    # 重放不含快照已覆盖的 1~3，也不丢 4~6
    assert sequences == [4, 5, 6]
    assert events[-1] == "done"
    assert events.count("done") == 1
    # 历史查询从快照序号起（快照是权威游标，不是客户端游标）
    assert subagent_streams["history"] == [("r-1", 3)]
    # run.finished 在重放中出现后流终止，finally 仍要退订
    assert len(subagent_streams["unsubscribed"]) == 1


@pytest.mark.asyncio
async def test_live_loop_skips_stale_events_below_cursor(monkeypatch, subagent_streams) -> None:
    """重连后迟到的低序号事件不重发；run.finished 不受游标过滤。"""
    snapshot = _fake_snapshot(sequence=3, terminal=False)
    response = await _open_subagent_stream(monkeypatch, snapshot, after_sequence=2)

    queue = subagent_streams["subscribed"][0][2]
    queue.put_nowait({"type": "message.updated", "sequence": 2})  # 迟到的旧事件
    queue.put_nowait({"type": "run.finished", "sequence": 4})

    frames = [_parse_frame(f) async for f in response.body_iterator]
    sequences = [d.get("sequence") for _, d in frames[1:] if d is not None and "sequence" in d]

    assert 2 not in sequences
    assert sequences[-1] == 4
    assert frames[-1][0] == "done"


@pytest.mark.asyncio
async def test_terminal_run_stream_skips_subscription(monkeypatch, subagent_streams) -> None:
    """终态 run 只发快照 + [DONE]，不建立订阅（关详情页前的收尾态）。"""
    snapshot = _fake_snapshot(sequence=9, terminal=True)
    response = await _open_subagent_stream(monkeypatch, snapshot, after_sequence=9)

    frames = [_parse_frame(f) async for f in response.body_iterator]

    assert [e for e, _ in frames] == ["run-snapshot", "done"]
    assert subagent_streams["subscribed"] == []
    assert subagent_streams["unsubscribed"] == []


# ---------------------------------------------------------------------------
# 接口级：followup / children catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_followup_endpoint_passes_through_and_maps_not_found(monkeypatch) -> None:
    from noesis.errors.exceptions import NotFoundException, ServiceException
    from noesis.schemas.chat_vo import SubagentFollowupRequest
    from noesis.services.subagent_session_service import SubagentSessionService

    monkeypatch.setattr(
        SubagentSessionService,
        "send_followup",
        AsyncMock(return_value={"session_id": "child-1", "status": "running"}),
    )
    ok = await chat_api.send_subagent_followup(
        "child-1",
        SubagentFollowupRequest(message="再查一下"),
        current_user=SimpleNamespace(user_id="u1"),
    )
    assert ok.status_code == 200
    assert json.loads(ok.body)["data"]["session_id"] == "child-1"

    # 子会话缺失抛 NotFoundException，交由全局异常处理器映射 404
    monkeypatch.setattr(
        SubagentSessionService,
        "send_followup",
        AsyncMock(side_effect=NotFoundException(message="子会话不存在")),
    )
    with pytest.raises(NotFoundException):
        await chat_api.send_subagent_followup(
            "child-none",
            SubagentFollowupRequest(message="再查一下"),
            current_user=SimpleNamespace(user_id="u1"),
        )

    # ServiceException 带「不存在」文案在端点内直接映射 404
    monkeypatch.setattr(
        SubagentSessionService,
        "send_followup",
        AsyncMock(side_effect=ServiceException(message="父会话不存在")),
    )
    missing = await chat_api.send_subagent_followup(
        "root-none",
        SubagentFollowupRequest(message="再查一下"),
        current_user=SimpleNamespace(user_id="u1"),
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_child_catalog_endpoint_exposes_child_session_identity(monkeypatch) -> None:
    """目录对 UI 只暴露 child session 身份；executor 内部 task id 不外泄。"""
    from noesis.services.agent_catalog_service import AgentCatalogService

    payload = {
        "tasks": [
            {
                "task_id": "child-1",
                "child_session_id": "child-1",
                "kind": "subagent",
                "status": "running",
            },
            {
                "task_id": "bg-1",
                "child_session_id": None,
                "kind": "shell",
                "status": "completed",
            },
        ]
    }
    monkeypatch.setattr(
        AgentCatalogService,
        "list_for_session",
        AsyncMock(return_value=payload),
    )
    response = await chat_api.list_bg_tasks(
        "root-1",
        current_user=SimpleNamespace(user_id="u1"),
        db=SimpleNamespace(),
    )
    body = json.loads(response.body)
    tasks = body["data"]["tasks"]
    subagent_entry = next(t for t in tasks if t["kind"] == "subagent")
    shell_entry = next(t for t in tasks if t["kind"] == "shell")
    # subagent 的公开身份是 child session（bg-* 内部 id 不出现）
    assert subagent_entry["task_id"] == subagent_entry["child_session_id"] == "child-1"
    assert not str(subagent_entry["task_id"]).startswith("bg-")
    # shell job 无会话身份，保留自身 id
    assert shell_entry["task_id"] == "bg-1"


# ---------------------------------------------------------------------------
# 同名并行独立 / 父会话软删级联（service 层）
# ---------------------------------------------------------------------------


class _LaunchDb:
    """launch / delete 用假 DB：记录 add 对象与 execute 语句。"""

    def __init__(self) -> None:
        self.all_added: list[Any] = []
        self.statements: list[Any] = []
        self._results: list[Any] = []

    def queue_result(self, result: Any) -> None:
        self._results.append(result)

    def add(self, value: Any) -> None:
        self.all_added.append(value)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> Any:
        self.statements.append(stmt)
        return self._results.pop(0)

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_parallel_same_name_launches_are_independent(monkeypatch) -> None:
    """同名子 Agent 并行调用：各自独立 child session / run，tool_call_id 一一对应。"""
    from noesis.services.chat_service import ChatService
    from noesis.services.subagent_session_service import SubagentSessionService
    from noesis.storage.postgres.models.chat import TAgentRun, TChatSession

    class _RunResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(id="parent-run-1")

    class _UpdateResult:
        rowcount = 1

    db = _LaunchDb()
    # 每次 launch：1×select 父 run + 2×update（child.created_by_run_id、parent.updated_at）
    for _ in range(2):
        db.queue_result(_RunResult())
        db.queue_result(_UpdateResult())
        db.queue_result(_UpdateResult())

    monkeypatch.setattr(
        ChatService, "get_session_by_id", AsyncMock(return_value=SimpleNamespace(id="root-1"))
    )

    launch_a = await SubagentSessionService.launch(
        parent_session_id="root-1", user_id="u1",
        description="政策检索", tool_call_id="call-a", db=db,
    )
    launch_b = await SubagentSessionService.launch(
        parent_session_id="root-1", user_id="u1",
        description="政策检索", tool_call_id="call-b", db=db,
    )

    # 独立身份
    assert launch_a.session_id != launch_b.session_id
    assert launch_a.run_id != launch_b.run_id

    sessions = [v for v in db.all_added if isinstance(v, TChatSession)]
    runs = [v for v in db.all_added if isinstance(v, TAgentRun)]
    assert len(sessions) == 2 and len(runs) == 2

    by_tool_call = {s.created_by_tool_call_id: s for s in sessions}
    assert set(by_tool_call) == {"call-a", "call-b"}
    # run 归属各自 session，不交叉
    for run in runs:
        assert run.session_id in {s.id for s in sessions}
    run_sessions = {run.session_id for run in runs}
    assert run_sessions == {launch_a.session_id, launch_b.session_id}


def _compile_sql(stmt: Any) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_parent_soft_delete_cascades_to_children(monkeypatch) -> None:
    """父会话软删：child session/消息级联软删 + 运行中任务与 run 取消。"""
    from noesis.agents.subagents import executor as executor_module
    from noesis.services import chat_service
    from noesis.services.chat_service import ChatService
    from noesis.services.scheduled_task_service import ScheduledTaskService

    class _SessionResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(id="root-1", user_id="u1")

    class _UpdateResult:
        rowcount = 2

    db = _LaunchDb()
    db.queue_result(_SessionResult())
    db.queue_result(_UpdateResult())
    db.queue_result(_UpdateResult())

    monkeypatch.setattr(
        ChatService, "_descendant_session_ids", AsyncMock(return_value=["child-1"])
    )
    cancel_calls: list[str] = []
    monkeypatch.setattr(
        chat_service,
        "cancel_session_agent_runs",
        AsyncMock(side_effect=lambda sid: cancel_calls.append(sid)),
    )
    monkeypatch.setattr(
        executor_module.BackgroundSubagentExecutor,
        "list_for_session",
        lambda session_id: [{"task_id": "bg-1", "status": "running"}],
    )
    cancel_task_calls: list[str] = []
    monkeypatch.setattr(
        executor_module.BackgroundSubagentExecutor,
        "cancel",
        lambda task_id: cancel_task_calls.append(task_id) or {"task_id": task_id, "status": "cancelled"},
    )
    monkeypatch.setattr(
        ScheduledTaskService, "disable_session_bound_tasks", AsyncMock()
    )
    monkeypatch.setattr(
        "noesis.agents.backends.sandbox_lifecycle.destroy_session_sandbox", AsyncMock()
    )
    monkeypatch.setattr(chat_service, "delete_session_workspace", lambda uid, sid: None)

    assert await ChatService.delete_session("root-1", "u1", db=db) is True

    # 运行中的 run 与后台任务均被取消（父 + 子各一次 run 取消；任务取消一次）
    assert cancel_calls == ["root-1", "child-1"]
    assert cancel_task_calls == ["bg-1"]

    updates = {}
    for stmt in db.statements:
        name = getattr(getattr(stmt, "table", None), "name", None)
        if name:
            updates.setdefault(name, []).append(_compile_sql(stmt))

    session_sql = "".join(updates["t_chat_session"])
    message_sql = "".join(updates["t_chat_message"])
    # 会话树（父+子）与消息都写了 deleted_at
    assert "root-1" in session_sql and "child-1" in session_sql
    assert "deleted_at" in session_sql
    assert "root-1" in message_sql and "child-1" in message_sql
    assert "deleted_at" in message_sql
