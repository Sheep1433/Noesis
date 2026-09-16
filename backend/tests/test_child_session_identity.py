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




@pytest.mark.asyncio
async def test_resume_hitl_rejects_with_409(monkeypatch) -> None:
    """子 Agent 无待审批操作：后台任务全自主（危险命令工具层拒绝），resume 入口只给明确 409。"""
    from noesis.errors.exceptions import ConflictException
    from noesis.services.subagent_session_service import SubagentSessionService

    class _Run:
        origin = "subagent"
        session_id = "s1"

    async def fake_get(run_id, user_id, db):
        return _Run()

    monkeypatch.setattr(SubagentSessionService, "_get_owned_run", staticmethod(fake_get))
    with pytest.raises(ConflictException) as exc_info:
        await SubagentSessionService.resume_hitl(
            run_id="r1", user_id="u1", decisions=[{"type": "approve"}], db=None,
        )
    assert "无待审批" in (exc_info.value.message or "")
