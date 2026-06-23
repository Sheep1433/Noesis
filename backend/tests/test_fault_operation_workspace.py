"""故障运维 Agent 会话工作区隔离测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.backends.aio_sandbox import AioSandboxBackend
from agent.fault_operation_agent import FaultOperationAgent


def test_workspace_backend_root_dir_matches_session(tmp_path: Path) -> None:
    from config import agent_workspace_paths as paths
    from config import user_data_paths as udp

    users_root = tmp_path / "users"
    fake_client = type("C", (), {})()

    with patch.object(udp, "_USERS_ROOT", users_root):
        paths.ensure_workspace_dir("u1", "fault-sess-1")
        backend = AioSandboxBackend(
            base_url="http://aio:8080",
            user_id="u1",
            session_id="fault-sess-1",
            root_dir="/workspace/sessions/fault-sess-1/workspace",
            client=fake_client,
        )

    assert backend._root_dir == "/workspace/sessions/fault-sess-1/workspace"
    assert (users_root / "u1" / "sessions" / "fault-sess-1" / "workspace").is_dir()


@pytest.mark.asyncio
async def test_run_agent_without_session_id_does_not_write_global_fault_ops(tmp_path: Path) -> None:
    from config import user_data_paths as udp

    users_root = tmp_path / "users"
    agent = FaultOperationAgent()

    with patch.object(udp, "_USERS_ROOT", users_root):
        chunks = []
        async for chunk in agent.run_agent("test query", session_id=None, current_user=None):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0]["finish_reason"] == "error"
    assert not users_root.exists() or not any(users_root.rglob("*"))
