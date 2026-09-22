"""记忆路径策略：agent 可见 ``/memory`` 路径的可读/可写白名单（单一事实来源）。

消费方：
- guardrails HITL（``memory_write_when``）：白名单内的写入才弹审批——
  白名单外的 /memory 写入引擎必然拒绝，直接放行让模型收到拒绝反馈，
  不拿注定失败的写入打扰用户。
- MemoryWriteMiddleware：白名单外的 /memory 写入在工具层直接拒绝
  （含 MEMORY.md 索引与 journal——引擎维护，模型只读）。
- MemoryFilesystemBackend.upload_files：upload 通道不经工具层中间件，
  白名单门卫在 backend 层执行。

键形态：与 CompositeBackend 派发到 memory backend 时一致（已剥
``/memory/`` 路由前缀的绝对路径，如 ``/preference/foo.md``）。
"""

from __future__ import annotations

import re

from noesis.memory.types import MEMORY_TYPES
from noesis.paths import AGENT_MEMORY_ROUTE, canonicalize_agent_path, posix_clean

MEMORY_ROOT_FILES = frozenset({"AGENTS.md", "USER.md"})
MEMORY_INDEX_FILE = "MEMORY.md"
MEMORY_TYPE_DIRS = frozenset(MEMORY_TYPES)
MEMORY_ENTRY_RE = re.compile(r"^/(%s)/([A-Za-z0-9_-]+)\.md$" % "|".join(MEMORY_TYPES))


def memory_key(file_path: str) -> str:
    """backend 键归一：轻量规范化，勿套 /workspace。"""
    text = (file_path or "").strip().replace("\\", "/")
    if not text.startswith("/"):
        text = f"/{text}"
    return posix_clean(text)


def is_memory_entry(key: str) -> bool:
    return bool(MEMORY_ENTRY_RE.match(key))


def is_memory_writable(key: str) -> bool:
    name = key.lstrip("/")
    return name in MEMORY_ROOT_FILES or is_memory_entry(key)


def strip_memory_route(path: str) -> str | None:
    """agent 可见完整路径 → /memory 路由内键；路由外返回 None。"""
    raw = (path or "").strip()
    if not raw:
        return None
    try:
        normalized = canonicalize_agent_path(raw)
    except ValueError:
        return None
    root = AGENT_MEMORY_ROUTE.rstrip("/")
    prefix = root + "/"
    if normalized == root:
        return "/"
    if normalized.startswith(prefix):
        return "/" + normalized[len(prefix):]
    return None


def is_memory_writable_path(path: str) -> bool:
    """agent 可见路径是否在记忆写入白名单内（根文件 + 五类条目目录）。"""
    key = strip_memory_route(path)
    return key is not None and is_memory_writable(key)


__all__ = [
    "MEMORY_ENTRY_RE",
    "MEMORY_INDEX_FILE",
    "MEMORY_ROOT_FILES",
    "MEMORY_TYPE_DIRS",
    "is_memory_entry",
    "is_memory_writable",
    "is_memory_writable_path",
    "memory_key",
    "strip_memory_route",
]
