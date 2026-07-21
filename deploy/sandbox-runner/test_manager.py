"""sandbox-runner manager / paths 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_resolve_host_data_dir_defaults_to_repo_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from paths import resolve_host_data_dir

    monkeypatch.delenv("NOESIS_HOST_DATA_DIR", raising=False)
    monkeypatch.setenv("NOESIS_REPO_ROOT", str(tmp_path))
    (tmp_path / "backend").mkdir()
    (tmp_path / "extensions").mkdir()
    assert resolve_host_data_dir() == (tmp_path / ".data").resolve()


def test_resolve_skills_host_dir_defaults_to_extensions_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from paths import resolve_skills_host_dir

    monkeypatch.delenv("SANDBOX_SKILLS_HOST_DIR", raising=False)
    monkeypatch.setenv("NOESIS_REPO_ROOT", str(tmp_path))
    (tmp_path / "backend").mkdir()
    (tmp_path / "extensions" / "skills").mkdir(parents=True)
    assert resolve_skills_host_dir() == (tmp_path / "extensions" / "skills").resolve()


def test_ensure_sandbox_mount_dirs_creates(tmp_path: Path) -> None:
    from paths import ensure_sandbox_mount_dirs

    ws = tmp_path / "workspace"
    skills = tmp_path / "skills"
    ensure_sandbox_mount_dirs(ws, skills, uid=10001, gid=10001)
    assert ws.is_dir()
    assert skills.is_dir()


def test_container_name_is_session_scoped() -> None:
    from manager import _container_name

    a = _container_name("u1", "s1")
    b = _container_name("u1", "s2")
    assert a != b
    assert a.startswith("noesis-sandbox-")
