"""本地 / 容器内默认路径解析（免手动 export）。"""

from __future__ import annotations

import os
from pathlib import Path

_RUNNER_DIR = Path(__file__).resolve().parent


def repo_root() -> Path:
    """推断仓库根目录（含 backend/ 与 extensions/）。"""
    env = os.environ.get("NOESIS_REPO_ROOT", "").strip()
    if env:
        return Path(env).resolve()

    for candidate in (_RUNNER_DIR.parent.parent, Path.cwd(), Path.cwd().parent):
        if (candidate / "backend").is_dir() and (candidate / "extensions").is_dir():
            return candidate.resolve()

    return _RUNNER_DIR.parent.parent.resolve()


def resolve_host_data_dir() -> Path:
    raw = os.environ.get("NOESIS_HOST_DATA_DIR", "").strip()
    if raw:
        return Path(raw).resolve()
    return (repo_root() / ".data").resolve()


def resolve_skills_host_dir() -> Path:
    raw = os.environ.get("SANDBOX_SKILLS_HOST_DIR", "").strip()
    if raw:
        return Path(raw).resolve()
    return (repo_root() / "extensions" / "skills").resolve()


_SANDBOX_MOUNT_DIR_MODE = 0o755
_SANDBOX_MOUNT_FILE_MODE = 0o644


def ensure_sandbox_mount_readable(path: Path, *, recursive: bool = False) -> None:
    """确保 bind mount 源对 AIO 进程用户 gem (uid=1000) 可读。"""
    if not path.exists():
        return
    try:
        path.chmod(_SANDBOX_MOUNT_DIR_MODE if path.is_dir() else _SANDBOX_MOUNT_FILE_MODE)
    except OSError:
        return
    if not recursive or not path.is_dir():
        return
    for child in path.rglob("*"):
        try:
            child.chmod(_SANDBOX_MOUNT_DIR_MODE if child.is_dir() else _SANDBOX_MOUNT_FILE_MODE)
        except OSError:
            continue
