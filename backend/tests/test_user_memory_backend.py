"""`/memory/` 路由与 MemoryWriteMiddleware 回归。

布局（openspec: md-memory-layer）：/memory 路由 = 用户 memory/ 子树整目录
（FilesystemBackend 全量复用）；写入策略（索引/journal 只读、条目白名单）
与写后索引同步在 MemoryWriteMiddleware（见 test_memory_recall.py 的中间件
用例），本文件钉路由层行为：挂载、隔离、迁移、grep 语义。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noesis.agents.backends.factory import build_agent_filesystem_backend
from noesis.paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_USER_FILE
from noesis.config import user_data_paths as user_paths
from noesis.memory.layout import ensure_user_memory_files
from noesis.memory.store import MemoryStore


@pytest.fixture()
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "users"
    monkeypatch.setattr(user_paths, "_USERS_ROOT", root)
    return root


def _backend(users_root: Path, uid: str = "u1", **kwargs):
    ensure_user_memory_files(uid)
    return build_agent_filesystem_backend(
        user_id=uid, session_id="test", sandbox=None, shell_timeout=30, **kwargs
    )


def test_memory_root_files_editable_via_route(users_root: Path) -> None:
    """根文件走 deepagents 全域语义：write=新建（seed 已存在则拒绝），
    edit=修改。Agent 的记忆更新指引本就是 edit_file，ReadBeforeWrite
    门卫同样要求先读后改。"""
    backend = _backend(users_root)
    agents_disk = user_paths.get_user_agents_md_path("u1")

    # seed 已存在：整文件 write 被拒（与 workspace/skills 路由一致）
    write_agents = backend.write(AGENT_MEMORY_AGENTS_FILE, "agents-v2")
    assert write_agents.error is not None

    # read → edit 链路可用
    read_agents = backend.read(AGENT_MEMORY_AGENTS_FILE)
    assert read_agents.error is None
    edit = backend.edit(AGENT_MEMORY_AGENTS_FILE, "## 关于我", "## 关于我\n\n- 新增一条")
    assert edit.error is None, edit
    on_disk = agents_disk.read_text(encoding="utf-8")
    assert "新增一条" in on_disk
    assert "工作偏好" in on_disk  # 其余内容未被覆盖

    # 新条目文件：write 新建可用
    write_entry = backend.write("/memory/preference/fresh.md", "正文")
    assert write_entry.error is None, write_entry


def test_legacy_root_files_migrated_into_memory(users_root: Path) -> None:
    """老布局（根文件在用户数据根上）首次 ensure 时搬入 memory/ 子树。"""
    u = users_root / "u1"
    u.mkdir(parents=True)
    (u / "AGENTS.md").write_text("老布局内容", encoding="utf-8")
    (u / "USER.md").write_text("画像", encoding="utf-8")

    ensure_user_memory_files("u1")
    assert (u / "memory" / "AGENTS.md").read_text(encoding="utf-8") == "老布局内容"
    assert (u / "memory" / "USER.md").read_text(encoding="utf-8") == "画像"
    assert not (u / "AGENTS.md").exists()
    assert not (u / "USER.md").exists()


@pytest.mark.asyncio
async def test_composite_memory_route_isolated_from_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from noesis.config.env import GetConfig

    # sandbox.backend 已无环境变量开关，直接替换配置读取方法强制 local_shell
    monkeypatch.setattr(
        GetConfig,
        "get_sandbox_config",
        lambda self: SimpleNamespace(
            backend="local_shell",
            runner_url="http://127.0.0.1:8090",
            execute_timeout_seconds=120,
        ),
    )

    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(user_paths, "_USERS_ROOT", users_root)
    monkeypatch.setattr(
        "noesis.agents.backends.factory.skills_root",
        lambda: platform,
    )

    ensure_user_memory_files("u1")
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


def test_upload_channel_whitelist_guard(users_root: Path) -> None:
    """upload 不经工具层中间件，白名单门卫在 backend 层执行。"""
    backend = _backend(users_root)
    responses = backend.upload_files([
        ("/memory/preference/via-upload.md", "正文".encode("utf-8")),
        ("/memory/MEMORY.md", b"x"),
        ("/memory/journal/2026-09-19.md", b"x"),
        ("/memory/junk.md", b"x"),
    ])
    by_path = {r.path: r for r in responses}
    assert by_path["/memory/preference/via-upload.md"].error is None
    assert by_path["/memory/MEMORY.md"].error == "permission_denied"
    assert by_path["/memory/journal/2026-09-19.md"].error == "permission_denied"
    assert by_path["/memory/junk.md"].error == "permission_denied"


def test_grep_covers_memory_entries_and_scoped_dirs(users_root: Path) -> None:
    """grep 候选集必须覆盖五类目录条目；目录路径要展开、不掺根文件。"""
    MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="文档格式",
        body="文档输出一律表格化、简体中文。", sources=[])
    MemoryStore.upsert_entry(
        "u1", memory_type="experience", label="无关",
        body="完全不相关的内容。", sources=[])
    backend = _backend(users_root)

    # 根路径：条目正文可命中（此前只有 MEMORY.md 索引行可见）
    g = backend.grep("表格化", path="/")
    entry_hits = [m for m in g.matches
                  if str(m.get("path", "")).startswith("/memory/preference/")]
    assert entry_hits, f"条目正文未命中: {g.matches}"
    assert any("表格化" in str(m.get("text", "")) for m in entry_hits)

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
    backend = _backend(users_root)
    g = backend.grep("wedding|marry|married|engaged", path="/")
    assert any("upcoming wedding" in str(m.get("text", "")) for m in g.matches), (
        f"正则交替模式未命中: {g.matches}"
    )


def test_grep_supports_recursive_glob_and_skips_binary_symlink(
    users_root: Path,
) -> None:
    """模型惯用的 **/*.md 必须命中；二进制文件跳过；越界 symlink 不进检索。"""
    import os

    MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="命名", body="_alpha_body_", sources=[])
    (users_root / "u1" / "memory" / "preference" / "bin.md").write_bytes(
        b"\xff\xfe\x00binary_alpha")
    evil = users_root / "u1" / "memory" / "preference" / "evil.md"
    evil.symlink_to("/etc/hostname")
    backend = _backend(users_root)

    g = backend.grep("alpha", path="/memory", glob="**/*.md")
    paths = [str(m.get("path", "")) for m in g.matches]
    assert any(p.endswith("preference/a.md") for p in paths), paths
    assert not any("bin.md" in p for p in paths)
    assert not any("evil.md" in p for p in paths)


def test_legacy_migration_overwrites_empty_target(users_root: Path) -> None:
    """老布局有内容而新位置是空文件时，迁移仍发生（内容不滞留丢失）。"""
    u = users_root / "u1"
    (u / "memory").mkdir(parents=True)
    (u / "memory" / "AGENTS.md").write_text("", encoding="utf-8")
    (u / "AGENTS.md").write_text("老布局内容", encoding="utf-8")

    ensure_user_memory_files("u1")
    assert "老布局内容" in (u / "memory" / "AGENTS.md").read_text(encoding="utf-8")
