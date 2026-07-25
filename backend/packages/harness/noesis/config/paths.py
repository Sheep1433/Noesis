"""仓库根目录与本地运行时数据路径（``.data/``）。"""

from __future__ import annotations

import os
from pathlib import Path

def _resolve_backend_dir() -> Path:
    if raw := os.environ.get("NOESIS_BACKEND_DIR", "").strip():
        return Path(raw).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, cwd / "backend", Path(__file__).resolve().parents[4]):
        if (candidate / "config.yaml").is_file() or (candidate / "pyproject.toml").is_file():
            return candidate
    return cwd


BACKEND_DIR = _resolve_backend_dir()
REPO_ROOT = BACKEND_DIR.parent if (BACKEND_DIR.parent / "extensions").is_dir() else BACKEND_DIR

# Compose：NOESIS_DATA_DIR=/data/noesis（与宿主机 NOESIS_HOST_DATA_DIR 同一目录）
_DATA_DIR_ENV = os.environ.get("NOESIS_DATA_DIR", "").strip()
DATA_DIR = Path(_DATA_DIR_ENV).resolve() if _DATA_DIR_ENV else (REPO_ROOT / ".data")


def data_path(*parts: str) -> Path:
    """返回 ``.data/`` 下路径并确保父目录存在。"""
    path = DATA_DIR.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def resolve_backend_relative(path_str: str) -> Path:
    """将配置中的相对路径解析为基于 ``backend/`` 的绝对路径。"""
    raw = Path(path_str).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    return (BACKEND_DIR / raw).resolve()
