"""沙箱 runner 侧：将平台 skills 链接进用户 skills 目录（与 backend 逻辑一致）。"""

from __future__ import annotations

from pathlib import Path

from paths import resolve_skills_host_dir


def ensure_user_skills_catalog(user_skills_dir: Path, *, platform_root: Path | None = None) -> Path:
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    platform = (platform_root or resolve_skills_host_dir()).resolve()
    if not platform.is_dir():
        return user_skills_dir

    for child in sorted(platform.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        link = user_skills_dir / child.name
        if link.exists():
            continue
        try:
            link.symlink_to(child.resolve(), target_is_directory=True)
        except OSError:
            continue
    return user_skills_dir
