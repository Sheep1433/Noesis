"""`/memory/` 虚拟路径与 UserMemoryBackend 回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.backends.agent_filesystem import UserMemoryBackend, build_agent_filesystem_backend
from agent.backends.mount_paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_USER_FILE
from config import user_data_paths as user_paths


def test_user_memory_backend_agents_and_user_writable(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    user = tmp_path / "USER.md"
    agents.write_text("agents-v1", encoding="utf-8")
    user.write_text("profile", encoding="utf-8")
    backend = UserMemoryBackend(agents_path=agents, user_path=user)

    read_agents = backend.read("/AGENTS.md")
    assert read_agents.error is None
    assert "agents-v1" in read_agents.file_data["content"]  # type: ignore[index]

    write_user = backend.write("/USER.md", "profile-v2")
    assert write_user.error is None
    assert user.read_text(encoding="utf-8") == "profile-v2"

    write_agents = backend.write("/AGENTS.md", "agents-v2")
    assert write_agents.error is None
    assert agents.read_text(encoding="utf-8") == "agents-v2"


def test_user_memory_download_files(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    user = tmp_path / "USER.md"
    agents.write_text("x", encoding="utf-8")
    user.write_text("y", encoding="utf-8")
    backend = UserMemoryBackend(agents_path=agents, user_path=user)
    responses = backend.download_files(["/AGENTS.md", "/USER.md"])
    assert responses[0].error is None
    assert responses[0].content == b"x"
    assert responses[1].content == b"y"


@pytest.mark.asyncio
async def test_composite_memory_route_isolated_from_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_BACKEND", "local_shell")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()

    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(user_paths, "_USERS_ROOT", users_root)
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    user_paths.ensure_user_memory_files("u1")
    agents_disk = user_paths.get_user_agents_md_path("u1")
    agents_disk.write_text("memory-body", encoding="utf-8")

    from agent.backends.factory import create_agent_backend

    backend = await create_agent_backend("u1", "s1")
    mem = backend.read(AGENT_MEMORY_AGENTS_FILE)
    assert mem.error is None
    assert "memory-body" in mem.file_data["content"]  # type: ignore[index]

    ws = backend.write("/research/notes.md", "task")
    assert ws.error is None
    workspace = user_paths.get_workspace_dir("u1", "s1")
    assert (workspace / "research" / "notes.md").read_text(encoding="utf-8") == "task"
    assert agents_disk.read_text(encoding="utf-8") == "memory-body"

    user_paths.delete_session_data("u1", "s1")
    assert not workspace.parent.exists()
    assert agents_disk.is_file()
    assert agents_disk.read_text(encoding="utf-8") == "memory-body"

    profile = backend.read(AGENT_MEMORY_USER_FILE)
    assert profile.error is None
