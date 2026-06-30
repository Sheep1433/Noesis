"""用户记忆路径与 seed 回归。"""

from __future__ import annotations

import pytest

from config import user_data_paths as paths


def test_get_user_agents_md_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    assert paths.get_user_agents_md_path("42") == tmp_path / "users" / "42" / "AGENTS.md"


def test_get_user_profile_md_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    assert paths.get_user_profile_md_path("42") == tmp_path / "users" / "42" / "USER.md"


def test_ensure_user_memory_files_creates_seed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    root = paths.ensure_user_memory_files("u1")
    assert root.is_dir()
    agents = paths.get_user_agents_md_path("u1")
    profile = paths.get_user_profile_md_path("u1")
    assert agents.is_file()
    assert profile.is_file()
    assert "工作偏好" in agents.read_text(encoding="utf-8")


def test_ensure_user_memory_files_idempotent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    paths.ensure_user_memory_files("u1")
    agents = paths.get_user_agents_md_path("u1")
    agents.write_text("custom", encoding="utf-8")
    paths.ensure_user_memory_files("u1")
    assert agents.read_text(encoding="utf-8") == "custom"


def test_invalid_user_id_rejected() -> None:
    with pytest.raises(ValueError):
        paths.get_user_agents_md_path("../evil")
