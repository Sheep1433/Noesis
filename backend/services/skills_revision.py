"""用户 Skills 内容版本：上传/删除后 bump，促使 SkillsMiddleware 重扫。"""

from __future__ import annotations

import time
from pathlib import Path

from config.user_data_paths import get_user_skills_dir

_REVISION_FILE = ".skills_revision"


def skills_revision_path(user_id: str) -> Path:
    return get_user_skills_dir(user_id) / _REVISION_FILE


def get_user_skills_revision(user_id: str) -> str:
    path = skills_revision_path(user_id)
    if not path.is_file():
        return "0"
    try:
        return path.read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


def bump_user_skills_revision(user_id: str) -> str:
    """个人 Skills 树变更后调用；返回新 revision。"""
    path = skills_revision_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = str(time.time_ns())
    path.write_text(value, encoding="utf-8")
    return value
