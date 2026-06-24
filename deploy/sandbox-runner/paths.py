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
