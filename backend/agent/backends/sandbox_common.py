"""沙箱 backend 共用小工具（mutex、写文件 payload）。"""

from __future__ import annotations

import base64
import threading

_MUTEX_REGISTRY: dict[tuple[str, str], threading.Lock] = {}
_MUTEX_REGISTRY_LOCK = threading.Lock()


def session_mutex(user_id: str, session_id: str) -> threading.Lock:
    key = (user_id, session_id)
    with _MUTEX_REGISTRY_LOCK:
        lock = _MUTEX_REGISTRY.get(key)
        if lock is None:
            lock = threading.Lock()
            _MUTEX_REGISTRY[key] = lock
        return lock


def prepare_write_file_payload(content: bytes) -> tuple[str, str | None]:
    """deepagents upload_files 传 bytes；下游 write API 要 str + 可选 encoding。"""
    try:
        return content.decode("utf-8"), None
    except UnicodeDecodeError:
        return base64.b64encode(content).decode("ascii"), "base64"
