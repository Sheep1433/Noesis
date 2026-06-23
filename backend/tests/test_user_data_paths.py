"""用户数据路径模块回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from config import user_data_paths as paths


@pytest.fixture
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "users"
    monkeypatch.setattr(paths, "_USERS_ROOT", root)
    return root


def test_get_workspace_dir(users_root: Path) -> None:
    got = paths.get_workspace_dir("42", "sess-abc")
    assert got == users_root / "42" / "sessions" / "sess-abc" / "workspace"


def test_ensure_workspace_dir(users_root: Path) -> None:
    created = paths.ensure_workspace_dir("u1", "s1")
    assert created.is_dir()


def test_delete_session_data_idempotent(users_root: Path) -> None:
    paths.ensure_workspace_dir("u1", "s1")
    paths.ensure_session_uploads_dir("u1", "s1")
    paths.delete_session_data("u1", "s1")
    paths.delete_session_data("u1", "s1")
    assert not (users_root / "u1" / "sessions" / "s1").exists()


def test_user_skills_dir(users_root: Path) -> None:
    assert paths.get_user_skills_dir("7") == users_root / "7" / "skills"
