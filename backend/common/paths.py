"""仓库根目录与本地运行时数据路径（``.data/``）。"""

from __future__ import annotations

from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = _BACKEND_DIR
REPO_ROOT = _BACKEND_DIR.parent
DATA_DIR = REPO_ROOT / ".data"


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
