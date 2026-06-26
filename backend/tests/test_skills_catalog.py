"""skills_catalog：平台 skill 符号链接合并。"""

from __future__ import annotations

import pytest

from config import skills_catalog
from config import user_data_paths as paths


def test_ensure_user_skills_catalog_links_platform_skills(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    monkeypatch.setattr(paths, "_USERS_ROOT", users_root)

    platform.mkdir()
    (platform / "deep-research-v2").mkdir()
    (platform / "deep-research-v2" / "SKILL.md").write_text("x", encoding="utf-8")

    catalog = skills_catalog.ensure_user_skills_catalog("u1", platform_root=platform)
    link = catalog / "deep-research-v2"
    assert link.is_symlink()
    assert link.resolve() == (platform / "deep-research-v2").resolve()


def test_platform_symlink_not_overwritten_by_user_dir(tmp_path, monkeypatch) -> None:
    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    monkeypatch.setattr(paths, "_USERS_ROOT", users_root)

    platform.mkdir()
    (platform / "foo").mkdir()
    user_skills = paths.ensure_user_skills_dir("u1")
    (user_skills / "foo").mkdir()
    (user_skills / "foo" / "SKILL.md").write_text("user", encoding="utf-8")

    skills_catalog.ensure_user_skills_catalog("u1", platform_root=platform)
    assert (user_skills / "foo" / "SKILL.md").read_text(encoding="utf-8") == "user"
    assert not (user_skills / "foo").is_symlink()
