"""`/memory/` 虚拟路径与 UserMemoryBackend 回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from noesis.agents.backends.memory import UserMemoryBackend
from noesis.agents.backends.factory import build_agent_filesystem_backend
from noesis.agents.backends.paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_USER_FILE
from noesis.config import user_data_paths as user_paths
from noesis.services.memory.store import MemoryStore


@pytest.fixture()
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "users"
    monkeypatch.setattr(user_paths, "_USERS_ROOT", root)
    return root


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
    from noesis.config.env import get_config

    get_config.get_sandbox_config.cache_clear()

    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(user_paths, "_USERS_ROOT", users_root)
    monkeypatch.setattr(
        "noesis.agents.backends.factory.skills_root",
        lambda: platform,
    )

    user_paths.ensure_user_memory_files("u1")
    agents_disk = user_paths.get_user_agents_md_path("u1")
    agents_disk.write_text("memory-body", encoding="utf-8")

    from noesis.agents.backends.factory import create_agent_backend

    backend = await create_agent_backend("u1", "s1")
    mem = backend.read(AGENT_MEMORY_AGENTS_FILE)
    assert mem.error is None
    assert "memory-body" in mem.file_data["content"]  # type: ignore[index]

    ws = backend.write("/workspace/research/notes.md", "task")
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


def test_grep_covers_memory_entries_and_scoped_dirs(users_root: Path) -> None:
    """grep 候选集必须覆盖五类目录条目；目录路径要展开、不掺根文件。"""
    MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="文档格式",
        body="文档输出一律表格化、简体中文。", sources=[])
    MemoryStore.upsert_entry(
        "u1", memory_type="experience", label="无关",
        body="完全不相关的内容。", sources=[])
    backend = build_agent_filesystem_backend(
        user_id="u1", session_id="grep-test",
        sandbox=None, shell_timeout=30,
    )

    # 根路径：条目正文可命中（此前只有 MEMORY.md 索引行可见）
    g = backend.grep("表格化", path="/")
    entry_hits = [m for m in g.matches
                  if str(m.get("path", "")).startswith("/memory/preference/")]
    assert entry_hits, f"条目正文未命中: {g.matches}"
    assert any("表格化" in str(m.get("content", "")) for m in entry_hits)

    # 类型目录 scoped：只搜该目录，不掺根文件
    g2 = backend.grep("表格化", path="/memory/preference")
    assert g2.matches and all(
        str(m.get("path", "")).startswith("/memory/preference/") for m in g2.matches)

    # journal 可经 grep 命中
    MemoryStore.append_journal("u1", session_id="s1", text="今天试了 pnpm workspace")
    g3 = backend.grep("pnpm", path="/")
    assert any(str(m.get("path", "")).startswith("/memory/journal/") for m in g3.matches)


def test_grep_supports_regex_alternation(users_root: Path) -> None:
    MemoryStore.upsert_entry(
        "u1", memory_type="experience", label="婚礼",
        body="Congratulations to Rachel on her upcoming wedding!", sources=[])
    backend = build_agent_filesystem_backend(
        user_id="u1", session_id="grep-regex-test",
        sandbox=None, shell_timeout=30,
    )
    g = backend.grep("wedding|marry|married|engaged", path="/")
    assert any("upcoming wedding" in str(m.get("content", "")) for m in g.matches), (
        f"正则交替模式未命中: {g.matches}"
    )
