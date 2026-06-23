"""用户私有 Skills 目录（委托 `user_data_paths`）。"""

from __future__ import annotations

from pathlib import Path

from config.user_data_paths import ensure_user_skills_dir as _ensure_user_skills_dir
from config.user_data_paths import get_user_skills_dir as _get_user_skills_dir


def get_user_skills_dir(user_id: str | int) -> Path:
    """返回 `.data/users/{user_id}/skills/`（不创建）。"""
    return _get_user_skills_dir(user_id)


def ensure_user_skills_dir(user_id: str | int) -> Path:
    """创建并返回用户 Skills 目录。"""
    return _ensure_user_skills_dir(user_id)
