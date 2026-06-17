"""会话删除与工作区清理联动测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.chat_models import TChatSession


def _session(user_id: str, session_id: str) -> TChatSession:
    obj = TChatSession()
    obj.id = session_id
    obj.user_id = user_id
    obj.title = "测试"
    return obj


@pytest.mark.asyncio
async def test_delete_session_removes_workspace(tmp_path: Path) -> None:
    from config import agent_workspace_paths as paths
    from services.chat_service import ChatService

    root = tmp_path / "ws"
    user_id = "u1"
    session_id = "sess-del-1"

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = _session(user_id, session_id)
    db.execute = AsyncMock(return_value=result_mock)
    db.commit = AsyncMock()

    with patch.object(paths, "_resolve_root", return_value=root):
        paths.ensure_workspace_dir(user_id, session_id)
        session_dir = root / "users" / user_id / "sessions" / session_id
        assert session_dir.is_dir()

        ok = await ChatService.delete_session(session_id, user_id, db=db)

    assert ok is True
    assert not session_dir.exists()
