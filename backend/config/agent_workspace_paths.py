"""Agent 会话工作区路径（委托 `user_data_paths`）。"""

from __future__ import annotations

from pathlib import Path

from config.user_data_paths import (
    delete_session_data,
    ensure_user_root,
    ensure_workspace_dir,
    get_user_root,
    get_workspace_dir,
    validate_segment,
)

__all__ = [
    "validate_segment",
    "get_user_workspace_root",
    "get_workspace_dir",
    "ensure_workspace_dir",
    "ensure_user_root",
    "delete_session_workspace",
    "delete_session_data",
]


def get_user_workspace_root(user_id: str | int) -> Path:
    """返回用户数据根（AIO 挂载 `users/{uid}/` → 容器 `/workspace`）。"""
    return get_user_root(user_id)


def delete_session_workspace(user_id: str | int, session_id: str) -> None:
    """删除会话子树（含 workspace 与附件）。"""
    delete_session_data(user_id, session_id)
