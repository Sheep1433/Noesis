"""用户记忆布局初始化：seed 根文件 + 老布局一次性搬迁。

布局（openspec: md-memory-layer）：用户数据根的 ``memory/`` 子树是
``/memory`` 路由的完整挂载根——根文件（AGENTS.md/USER.md）、索引
（MEMORY.md）、五类条目目录、journal 全在其中。老布局曾把根文件放在
用户数据根上，``ensure_user_memory_files`` 负责搬迁与补 seed。
"""

from __future__ import annotations

from pathlib import Path

from noesis.runtime.logging import logger
from noesis.config.user_data_paths import (
    ensure_user_root,
    get_user_agents_md_path,
    get_user_root,
    get_user_profile_md_path,
)
from noesis.memory.store import MemoryStore

_AGENTS_MD_SEED = """<!-- Noesis 用户记忆：Agent 会在你明确要求「记住」时更新此文件 -->
## 关于我

## 工作偏好
"""

_USER_MD_SEED = """<!-- Noesis 用户画像：可在设置页或上下文面板编辑 -->
## 基本信息
"""

_ROOT_FILES = ("AGENTS.md", "USER.md")


def migrate_legacy_root_files(user_id: str | int) -> None:
    """老布局根文件（用户数据根上的 AGENTS.md/USER.md）搬入 memory/ 子树。

    新旧两处同时存在时保留新位置，老文件留在原地不动（避免覆盖更新的
    内容）；该情形只应出现在手工搬运出错时。
    """
    root = get_user_root(user_id)
    memory_root = root / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    for name in _ROOT_FILES:
        legacy = root / name
        target = memory_root / name
        if legacy.is_file() and not target.exists():
            legacy.replace(target)
            logger.info(
                "记忆根文件已迁入 memory/ 子树 user_id={} file={}", user_id, name
            )


def ensure_user_memory_files(user_id: str | int) -> Path:
    """创建用户记忆布局并 seed AGENTS.md / USER.md（若不存在）。"""
    root = ensure_user_root(user_id)
    migrate_legacy_root_files(user_id)
    MemoryStore.ensure_layout(user_id)
    agents = get_user_agents_md_path(user_id)
    if not agents.is_file():
        agents.write_text(_AGENTS_MD_SEED, encoding="utf-8")
    profile = get_user_profile_md_path(user_id)
    if not profile.is_file():
        profile.write_text(_USER_MD_SEED, encoding="utf-8")
    return root
