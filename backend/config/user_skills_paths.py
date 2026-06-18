"""用户私有 Skills 目录（`.data/user_skills/users/{user_id}/`）。"""

from __future__ import annotations

from pathlib import Path

from common.paths import DATA_DIR
from config.agent_workspace_paths import validate_segment

_USER_SKILLS_ROOT = DATA_DIR / "user_skills"


def get_user_skills_dir(user_id: str | int) -> Path:
    """返回用户 Skills 目录（不创建）。"""
    uid = validate_segment(str(user_id), kind="user_id")
    return _USER_SKILLS_ROOT / "users" / uid


def ensure_user_skills_dir(user_id: str | int) -> Path:
    """创建并返回用户 Skills 目录。"""
    path = get_user_skills_dir(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
