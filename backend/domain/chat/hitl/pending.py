"""进程内 pending HITL 状态（interrupt_id ↔ assistant_message_id）。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingHitl:
    interrupt_id: str
    session_id: str
    user_id: str
    assistant_message_id: str
    expires_at: float
    kind: str
    action_requests: list[dict[str, Any]] = field(default_factory=list)
    review_configs: list[dict[str, Any]] = field(default_factory=list)


class PendingHitlStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: dict[str, PendingHitl] = {}

    def put(self, pending: PendingHitl) -> None:
        with self._lock:
            self._by_session[pending.session_id] = pending

    def get(self, session_id: str) -> PendingHitl | None:
        with self._lock:
            return self._by_session.get(session_id)

    def pop_if_match(self, session_id: str, interrupt_id: str) -> PendingHitl | None:
        with self._lock:
            cur = self._by_session.get(session_id)
            if cur is None or cur.interrupt_id != interrupt_id:
                return None
            return self._by_session.pop(session_id)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)

    def is_expired(self, pending: PendingHitl, *, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= pending.expires_at


pending_hitl = PendingHitlStore()
