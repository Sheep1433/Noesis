"""会话删除与工作区清理联动测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from noesis.storage.postgres.models.chat import TChatSession


def _session(user_id: str, session_id: str) -> TChatSession:
    obj = TChatSession()
    obj.id = session_id
    obj.user_id = user_id
    obj.title = "测试"
    return obj


@pytest.mark.asyncio
async def test_delete_session_removes_workspace(tmp_path: Path) -> None:
    from noesis.config import user_data_paths as paths
    from noesis.config import user_data_paths as udp
    from noesis.services.chat_service import ChatService

    users_root = tmp_path / "users"
    user_id = "u1"
    session_id = "sess-del-1"

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = _session(user_id, session_id)
    db.execute = AsyncMock(return_value=result_mock)
    db.commit = AsyncMock()

    with (
        patch.object(udp, "_USERS_ROOT", users_root),
        patch("noesis.services.chat_service.cancel_session_agent_runs", new_callable=AsyncMock),
        # 与 sandbox_cleanup 测试对齐：单测不得向共享 sandbox runner 发真实 DELETE
        patch(
            "noesis.agents.backends.sandbox_lifecycle.destroy_session_sandbox",
            new_callable=AsyncMock,
        ),
    ):
        paths.ensure_workspace_dir(user_id, session_id)
        session_dir = users_root / user_id / "sessions" / session_id
        assert session_dir.is_dir()

        ok = await ChatService.delete_session(session_id, user_id, db=db)

    assert ok is True
    assert not session_dir.exists()
