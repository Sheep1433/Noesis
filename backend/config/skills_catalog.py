"""用户 Skills 目录：合并平台 skill 包（symlink）与用户上传。"""

from __future__ import annotations

from pathlib import Path

from common.logging import logger
from config.extensions_paths import skills_root
from config.user_data_paths import ensure_user_skills_dir, get_user_skills_dir


def ensure_user_skills_catalog(user_id: str | int, *, platform_root: Path | None = None) -> Path:
    """确保 `.data/users/{uid}/skills/` 含平台 skill 符号链接（与用户上传同级）。

    同名目录已存在时保留用户内容，不覆盖。
    """
    user_skills = ensure_user_skills_dir(user_id)
    platform = (platform_root or skills_root()).resolve()
    if not platform.is_dir():
        return user_skills

    linked = 0
    for child in sorted(platform.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        link = user_skills / child.name
        if link.exists():
            continue
        try:
            link.symlink_to(child.resolve(), target_is_directory=True)
            linked += 1
        except OSError as exc:
            logger.warning(
                "平台 skill 链接失败 user_id={} name={} err={}",
                user_id,
                child.name,
                exc,
            )

    if linked:
        logger.debug("已链接平台 skills user_id={} count={}", user_id, linked)
    return user_skills


def is_platform_skill_entry(user_id: str | int, skill_name: str) -> bool:
    """该 skill 名是否为指向平台目录的符号链接（只读）。"""
    if not skill_name or skill_name.startswith("."):
        return False
    entry = get_user_skills_dir(user_id) / skill_name
    return entry.is_symlink()
