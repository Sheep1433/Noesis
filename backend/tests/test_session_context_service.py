"""会话上下文 API 服务测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from config import user_data_paths as paths
from services.session_context_service import SessionContextService


@pytest.mark.asyncio
async def test_read_workspace_file_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    ws = paths.ensure_workspace_dir(uid, sid)
    (ws / "ok.md").write_text("hello", encoding="utf-8")
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        rel, content = await SessionContextService.read_workspace_file(
            sid, uid, "ok.md", db,
        )
        assert rel == "ok.md"
        assert content == "hello"

        with pytest.raises(HTTPException) as exc:
            await SessionContextService.read_workspace_file(sid, uid, "../etc/passwd", db)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_context_not_owned() -> None:
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(HTTPException) as exc:
            await SessionContextService.get_context("s1", "u1", db)
        assert exc.value.status_code == 404
