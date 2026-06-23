"""Agent 会话工作区路径模块回归测试（委托 user_data_paths）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.backends.local_shell import create_local_shell_backend
from config import agent_workspace_paths as paths
from config import user_data_paths as user_paths


@pytest.fixture
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "users"
    monkeypatch.setattr(user_paths, "_USERS_ROOT", root)
    return root


def test_get_workspace_dir_valid_path(users_root: Path) -> None:
    got = paths.get_workspace_dir("42", "sess-abc-123")
    assert got == users_root / "42" / "sessions" / "sess-abc-123" / "workspace"


def test_ensure_workspace_dir_creates_directory(users_root: Path) -> None:
    created = paths.ensure_workspace_dir("u1", "s1")
    assert created.is_dir()
    assert created == users_root / "u1" / "sessions" / "s1" / "workspace"


@pytest.mark.parametrize(
    "bad_value",
    ["../etc", "foo/bar", "..", "a/b", ""],
)
def test_validate_segment_rejects_path_injection(bad_value: str) -> None:
    with pytest.raises(ValueError, match="非法"):
        paths.validate_segment(bad_value, kind="session_id")


def test_delete_session_workspace_is_idempotent(users_root: Path) -> None:
    paths.ensure_workspace_dir("u1", "s1")
    paths.delete_session_workspace("u1", "s1")
    paths.delete_session_workspace("u1", "s1")
    assert not (users_root / "u1" / "sessions" / "s1").exists()


def test_users_with_same_session_id_are_isolated(users_root: Path) -> None:
    dir_u1 = paths.ensure_workspace_dir("u1", "abc")
    dir_u2 = paths.ensure_workspace_dir("u2", "abc")
    assert dir_u1 != dir_u2
    dir_u1.joinpath("notes.md").write_text("user1", encoding="utf-8")
    dir_u2.joinpath("notes.md").write_text("user2", encoding="utf-8")
    assert dir_u1.joinpath("notes.md").read_text(encoding="utf-8") == "user1"
    assert dir_u2.joinpath("notes.md").read_text(encoding="utf-8") == "user2"


def test_parallel_sessions_write_independent_files(users_root: Path) -> None:
    ws1 = paths.ensure_workspace_dir("u1", "s1")
    ws2 = paths.ensure_workspace_dir("u1", "s2")
    backend1 = create_local_shell_backend(ws1, virtual_mode=True)
    backend2 = create_local_shell_backend(ws2, virtual_mode=True)

    r1 = backend1.write("/notes.md", "session-one")
    r2 = backend2.write("/notes.md", "session-two")
    assert r1.error is None
    assert r2.error is None

    assert ws1.joinpath("notes.md").read_text(encoding="utf-8") == "session-one"
    assert ws2.joinpath("notes.md").read_text(encoding="utf-8") == "session-two"


def test_default_root_uses_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_dir = tmp_path / ".data"
    monkeypatch.setattr(user_paths, "_USERS_ROOT", data_dir / "users")
    got = paths.get_workspace_dir("1", "sess")
    assert got == data_dir / "users" / "1" / "sessions" / "sess" / "workspace"
