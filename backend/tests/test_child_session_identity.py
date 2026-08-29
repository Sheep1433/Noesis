"""子 Agent 会话身份响应契约。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from noesis.errors.exceptions import NotFoundException
from noesis.services.subagent_session_service import SubagentSessionService
from server.api.chat_api import _session_to_response


def test_child_session_has_stable_identity_metadata() -> None:
    session = SimpleNamespace(
        id="child-1",
        parent_id="root-1",
        kind="subagent",
        created_by_run_id="run-1",
        created_by_tool_call_id="call-1",
        user_id="user-1",
        title="政策检索",
        extra={"agent_profile": "task-worker"},
        created_at=1,
        updated_at=2,
        deleted_at=None,
        pinned=False,
        archived=False,
    )

    payload = _session_to_response(session).model_dump()

    assert payload["kind"] == "subagent"
    assert payload["parent_id"] == "root-1"
    assert payload["created_by_run_id"] == "run-1"
    assert payload["created_by_tool_call_id"] == "call-1"


class _LaunchFakeDb:
    """launch 用例测试替身：记录 add/flush 批次，execute 按序返回预置结果。"""

    def __init__(self, parent_run=None):
        self.pending: list = []
        self.flush_batches: list = []
        self.execute = AsyncMock(side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: parent_run),
            SimpleNamespace(),
            SimpleNamespace(),
        ])
        self.commit = AsyncMock()

    def add(self, value):
        self.pending.append(value)

    async def flush(self):
        self.flush_batches.append(list(self.pending))
        self.pending.clear()


@pytest.mark.asyncio
async def test_launch_flushes_child_session_before_messages(monkeypatch) -> None:
    """child FK 必须先落库，否则真实 PostgreSQL 会拒绝首轮消息。"""
    from noesis.services.chat_service import ChatService

    db = _LaunchFakeDb(parent_run=SimpleNamespace(id="parent-run-1"))
    monkeypatch.setattr(
        ChatService, "get_session_by_id", AsyncMock(return_value=SimpleNamespace(id="root-1"))
    )

    await SubagentSessionService.launch(
        parent_session_id="root-1",
        user_id="user-1",
        description="验证 child FK",
        tool_call_id="call-1",
        db=db,
    )

    assert len(db.flush_batches) >= 3
    assert len(db.flush_batches[0]) == 1
    assert db.flush_batches[0][0].__class__.__name__ == "TChatSession"


@pytest.mark.asyncio
async def test_launch_persists_worker_model_id(monkeypatch) -> None:
    """launch 落库 extra.model_id：子会话详情的模型选择器显示 worker 实际模型，
    而不是回落到全局默认模型。"""
    from noesis.services.chat_service import ChatService

    db = _LaunchFakeDb()
    monkeypatch.setattr(
        ChatService, "get_session_by_id", AsyncMock(return_value=SimpleNamespace(id="root-1"))
    )

    await SubagentSessionService.launch(
        parent_session_id="root-1",
        user_id="user-1",
        description="模型持久化",
        model_id="deepseek-v4-flash",
        db=db,
    )

    child = db.flush_batches[0][0]
    assert child.extra["model_id"] == "deepseek-v4-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "extra"),
    [
        ("stop_run", {}),
        ("resume_hitl", {"decisions": []}),
    ],
)
async def test_missing_subagent_run_raises_not_found(method_name: str, extra: dict) -> None:
    from unittest.mock import AsyncMock, MagicMock

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    method = getattr(SubagentSessionService, method_name)

    with pytest.raises(NotFoundException) as exc_info:
        await method(run_id="missing", user_id="user-1", db=db, **extra)
    assert exc_info.value.message == "子 Agent run 不存在"


@pytest.mark.asyncio
async def test_resume_hitl_normalizes_pydantic_decisions(monkeypatch) -> None:
    """API 层传入的 HitlDecisionItem 必须归一化为纯 dict。

    executor 的 resume 载荷直接进 langchain HITL 中间件（按下标取值），
    pydantic 对象会 TypeError 崩掉整个子 Agent（用户审批拒绝即失败）。
    """
    from unittest.mock import AsyncMock, MagicMock

    from noesis.schemas.qa_vo import HitlDecisionItem
    from noesis.services import subagent_session_service as svc

    run = SimpleNamespace(id="run-1", session_id="child-1", origin="subagent")
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    db = SimpleNamespace(execute=AsyncMock(), rollback=AsyncMock())

    submitted: list = []

    class _Port:
        @staticmethod
        def submit_decisions(task_id, decisions):
            submitted.append((task_id, decisions))
            return {"task_id": task_id}

    import noesis.services.subagent_runtime_port as port

    monkeypatch.setattr(port, "ExecutorPort", _Port, raising=False)
    monkeypatch.setattr(
        svc, "ExecutorPort", _Port, raising=False
    )
    # _wait_run 首轮查询即命中：状态已脱离 hitl_pending
    terminal = SimpleNamespace(status="running")
    terminal_result = MagicMock()
    terminal_result.scalar_one_or_none.return_value = terminal
    db.execute = AsyncMock(side_effect=[run_result, terminal_result])

    await svc.SubagentSessionService.resume_hitl(
        run_id="run-1",
        user_id="user-1",
        decisions=[HitlDecisionItem(type="reject", message="用户拒绝了该操作")],
        db=db,
    )

    assert submitted == [("child-1", [{"type": "reject", "message": "用户拒绝了该操作"}])]


@pytest.mark.asyncio
async def test_mark_waiting_approval_updates_assistant_message(monkeypatch) -> None:
    """进入待审批必须原子更新 run 与 assistant 消息投影。

    消息不更新的话重开抽屉时被中断工具段仍是 running（扫光），
    与等待审批的事实不符。
    """
    from unittest.mock import AsyncMock, MagicMock

    from noesis.services import subagent_session_service as svc

    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        lambda: _Ctx(),
    )

    content = {"version": 1, "parts": [
        {"type": "tool", "tool_call_id": "c1", "state": "approval_pending"},
    ]}
    interrupt = {"interrupt_id": "iid", "action_requests": [{"name": "write_file", "tool_call_id": "c1"}]}

    await svc.SubagentSessionService.mark_waiting_approval(
        "run-1", interrupt, content=content, sequence=3, assistant_message_id="msg-1",
    )

    assert db.execute.await_count == 2
    run_stmt, message_stmt = [call.args[0] for call in db.execute.await_args_list]
    assert run_stmt.table.name == "t_agent_run"
    assert message_stmt.table.name == "t_chat_message"
    assert content in message_stmt.compile().params.values()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_waiting_approval_skips_message_when_run_guard_misses(monkeypatch) -> None:
    """run 守卫未命中（迟到投影）时回滚，不得再单独更新消息造成两表分叉。"""
    from unittest.mock import AsyncMock, MagicMock

    from noesis.services import subagent_session_service as svc

    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        lambda: _Ctx(),
    )

    await svc.SubagentSessionService.mark_waiting_approval(
        "run-1", {"interrupt_id": "iid"}, content={"parts": []}, sequence=3,
        assistant_message_id="msg-1",
    )

    db.execute.assert_awaited_once()
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
