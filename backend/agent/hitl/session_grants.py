"""进程内 HITL session grant（网络类 execute；memory 写入不可 grant）。"""

from __future__ import annotations

import threading
from typing import Literal

GrantKind = Literal["network_execute"]


class SessionGrantStore:
    """线程安全的进程内 grant 集；会话结束或进程重启后失效。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._grants: dict[str, set[GrantKind]] = {}

    def grant(self, session_id: str, kind: GrantKind = "network_execute") -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        with self._lock:
            self._grants.setdefault(sid, set()).add(kind)

    def has_network_grant(self, session_id: str) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        with self._lock:
            return "network_execute" in self._grants.get(sid, set())

    def clear(self, session_id: str) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        with self._lock:
            self._grants.pop(sid, None)

    def clear_all(self) -> None:
        with self._lock:
            self._grants.clear()


session_grants = SessionGrantStore()
