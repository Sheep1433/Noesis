"""Skills 文件目录服务回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from config import user_data_paths as paths
from services.skill_fs_service import SkillFsService


@pytest.fixture
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "users"
    monkeypatch.setattr(paths, "_USERS_ROOT", root)
    return root


def test_delete_user_skill_package_success(users_root: Path) -> None:
    skill_dir = paths.ensure_user_skills_dir("u1") / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo", encoding="utf-8")

    ok, msg = SkillFsService.delete_user_skill_package("my-skill", "u1")

    assert ok is True
    assert "my-skill" in msg
    assert not skill_dir.exists()


def test_delete_user_skill_package_rejects_nested_path(users_root: Path) -> None:
    root = paths.ensure_user_skills_dir("u1")
    nested = root / "my-skill" / "scripts"
    nested.mkdir(parents=True)

    ok, msg = SkillFsService.delete_user_skill_package("my-skill/scripts", "u1")

    assert ok is False
    assert nested.exists()


def test_delete_user_skill_package_rejects_missing(users_root: Path) -> None:
    paths.ensure_user_skills_dir("u1")

    ok, msg = SkillFsService.delete_user_skill_package("missing", "u1")

    assert ok is False
    assert "不存在" in msg
