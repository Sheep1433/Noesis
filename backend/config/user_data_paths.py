"""用户级运行时数据路径（`.data/users/{user_id}/`）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from common.logging import logger
from common.paths import DATA_DIR

_USERS_ROOT = DATA_DIR / "users"

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_segment(name: str, *, kind: str = "segment") -> str:
    """校验路径段仅含 [A-Za-z0-9_-]，防止目录穿越。"""
    if not name or not _SEGMENT_RE.match(name):
        raise ValueError(f"非法 {kind}: {name!r}，仅允许 [A-Za-z0-9_-]")
    return name


def get_user_root(user_id: str | int) -> Path:
    """返回用户数据根 `.data/users/{user_id}/`（不创建）。"""
    uid = validate_segment(str(user_id), kind="user_id")
    return _USERS_ROOT / uid


def get_user_skills_dir(user_id: str | int) -> Path:
    """返回用户 Skills 目录 `.data/users/{user_id}/skills/`（不创建）。"""
    return get_user_root(user_id) / "skills"


def ensure_user_skills_dir(user_id: str | int) -> Path:
    """创建并返回用户 Skills 目录。"""
    path = get_user_skills_dir(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_session_root(user_id: str | int, session_id: str) -> Path:
    """返回会话子树根 `.data/users/{user_id}/sessions/{session_id}/`（不创建）。"""
    uid = validate_segment(str(user_id), kind="user_id")
    sid = validate_segment(session_id, kind="session_id")
    return get_user_root(uid) / "sessions" / sid


def get_workspace_dir(user_id: str | int, session_id: str) -> Path:
    """返回 Agent 工作区目录（不创建）。"""
    return get_session_root(user_id, session_id) / "workspace"


def ensure_workspace_dir(user_id: str | int, session_id: str) -> Path:
    """创建并返回 Agent 工作区目录。"""
    path = get_workspace_dir(user_id, session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_session_uploads_dir(user_id: str | int, session_id: str) -> Path:
    """返回会话附件原文件目录（不创建）。"""
    return get_session_root(user_id, session_id) / "uploads"


def ensure_session_uploads_dir(user_id: str | int, session_id: str) -> Path:
    path = get_session_uploads_dir(user_id, session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_session_attachments_dir(user_id: str | int, session_id: str) -> Path:
    """返回会话附件 Markdown 副本目录（不创建）。"""
    return get_session_root(user_id, session_id) / "attachments"


def ensure_session_attachments_dir(user_id: str | int, session_id: str) -> Path:
    path = get_session_attachments_dir(user_id, session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete_session_data(user_id: str | int, session_id: str) -> None:
    """删除会话子树（workspace + uploads + attachments），幂等。"""
    session_dir = get_session_root(user_id, session_id)
    if not session_dir.is_dir():
        return
    shutil.rmtree(session_dir)
    logger.info(
        "已删除会话数据 user_id=%s session_id=%s path=%s",
        user_id,
        session_id,
        session_dir,
    )
