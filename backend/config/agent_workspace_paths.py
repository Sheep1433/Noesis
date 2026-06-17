"""Agent 会话工作区路径解析与生命周期。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from common.logging import logger
from common.paths import DATA_DIR

_WORKSPACE_ROOT = DATA_DIR / "agent_workspace"

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_segment(name: str, *, kind: str = "segment") -> str:
    """校验路径段仅含 [A-Za-z0-9_-]，防止目录穿越。"""
    if not name or not _SEGMENT_RE.match(name):
        raise ValueError(f"非法 {kind}: {name!r}，仅允许 [A-Za-z0-9_-]")
    return name


def _resolve_root() -> Path:
    return _WORKSPACE_ROOT


def get_workspace_dir(user_id: str | int, session_id: str) -> Path:
    """返回会话工作区目录（不创建）。"""
    uid = validate_segment(str(user_id), kind="user_id")
    sid = validate_segment(session_id, kind="session_id")
    return _resolve_root() / "users" / uid / "sessions" / sid / "workspace"


def ensure_workspace_dir(user_id: str | int, session_id: str) -> Path:
    """创建并返回会话工作区目录。"""
    path = get_workspace_dir(user_id, session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete_session_workspace(user_id: str | int, session_id: str) -> None:
    """删除会话工作区整棵子树（幂等）。"""
    uid = validate_segment(str(user_id), kind="user_id")
    sid = validate_segment(session_id, kind="session_id")
    session_dir = _resolve_root() / "users" / uid / "sessions" / sid
    if not session_dir.is_dir():
        return
    shutil.rmtree(session_dir)
    logger.info(
        "已删除会话工作区 user_id=%s session_id=%s path=%s",
        uid,
        sid,
        session_dir,
    )
