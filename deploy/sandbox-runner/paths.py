"""本地 / 容器内默认路径解析（免手动 export）。

Compose 部署时：
- NOESIS_HOST_DATA_DIR / SANDBOX_SKILLS_HOST_DIR 必须是 Docker daemon 可见的**宿主机绝对路径**。
- runner 容器内需把同一路径 bind 进来（路径字符串与宿主机一致），才能 mkdir/chown。
"""

from __future__ import annotations

import os
import stat
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


def ensure_sandbox_mount_dirs(
    *paths: Path,
    uid: int = 10001,
    gid: int = 10001,
) -> None:
    """创建挂载源目录；尽量 chown 到沙箱 UID，保留既有可执行位。

    不再递归 chmod 644（会破坏 Skill 脚本可执行位）。
    """
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chown(path, uid, gid)
        except OSError:
            # 非 root runner 无法 chown 时放宽目录权限以便沙箱用户写入 workspace
            try:
                path.chmod(path.stat().st_mode | stat.S_IWOTH | stat.S_IXOTH | stat.S_IROTH)
            except OSError:
                pass
