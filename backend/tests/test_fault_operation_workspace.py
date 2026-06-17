"""故障运维 Agent 会话工作区隔离测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.fault_operation_agent import FaultOperationAgent, _build_fault_backend


def test_build_fault_backend_uses_session_workspace(tmp_path: Path) -> None:
    from config import agent_workspace_paths as paths

    root = tmp_path / "agent_workspace"
    with patch.object(paths, "_resolve_root", return_value=root):
        backend = _build_fault_backend("u1", "fault-sess-1")
        backend.write("/notes.md", "fault notes")

    notes = root / "users" / "u1" / "sessions" / "fault-sess-1" / "workspace" / "notes.md"
    assert notes.is_file()
    assert notes.read_text(encoding="utf-8") == "fault notes"
    assert not (root / "fault_ops").exists()


@pytest.mark.asyncio
async def test_run_agent_without_session_id_does_not_write_global_fault_ops(tmp_path: Path) -> None:
    from config import agent_workspace_paths as paths

    root = tmp_path / "agent_workspace"
    agent = FaultOperationAgent()

    with patch.object(paths, "_resolve_root", return_value=root):
        chunks = []
        async for chunk in agent.run_agent("test query", session_id=None, current_user=None):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0]["finish_reason"] == "error"
    assert not root.exists() or not any(root.rglob("*"))
