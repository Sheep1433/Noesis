"""子 Agent 会话身份响应契约。"""

from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_launch_flushes_child_session_before_messages(monkeypatch) -> None:
    """child FK 必须先落库，否则真实 PostgreSQL 会拒绝首轮消息。"""
    from unittest.mock import AsyncMock

    from noesis.services.chat_service import ChatService
    from noesis.services.subagent_session_service import SubagentSessionService

    parent = SimpleNamespace(id="root-1")
    parent_run = SimpleNamespace(id="parent-run-1")

    class Result:
        def scalar_one_or_none(self):
            return parent_run

    class Db:
        def __init__(self):
            self.pending = []
            self.flush_batches = []
            self.execute = AsyncMock(side_effect=[Result(), SimpleNamespace(), SimpleNamespace()])
            self.commit = AsyncMock()

        def add(self, value):
            self.pending.append(value)

        async def flush(self):
            self.flush_batches.append(list(self.pending))
            self.pending.clear()

    db = Db()
    monkeypatch.setattr(ChatService, "get_session_by_id", AsyncMock(return_value=parent))

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
