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


def test_user_tree_skips_platform_skill_symlinks(users_root: Path, tmp_path: Path) -> None:
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    (platform / "deep-research-v2").mkdir()
    (platform / "deep-research-v2" / "SKILL.md").write_text("# platform", encoding="utf-8")

    user_skills = paths.ensure_user_skills_dir("u1")
    (user_skills / "my-skill").mkdir()
    (user_skills / "my-skill" / "SKILL.md").write_text("# user", encoding="utf-8")
    (user_skills / "deep-research-v2").symlink_to(
        platform / "deep-research-v2", target_is_directory=True
    )

    tree = SkillFsService.get_tree("u1")
    user_labels = [node.label for node in tree.user.tree]

    assert user_labels == ["my-skill"]


def test_delete_user_skill_package_rejects_symlink(users_root: Path, tmp_path: Path) -> None:
    platform = tmp_path / "platform-skills" / "linked-skill"
    platform.mkdir(parents=True)
    link = paths.ensure_user_skills_dir("u1") / "linked-skill"
    link.symlink_to(platform, target_is_directory=True)

    ok, msg = SkillFsService.delete_user_skill_package("linked-skill", "u1")

    assert ok is False
    assert "链接" in msg
    assert link.is_symlink()
