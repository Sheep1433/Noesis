"""按 session/run 保存最新上下文指标快照。"""
from __future__ import annotations

from threading import Lock
from typing import Any


class ContextMetricsRegistry:
    """暂存上下文快照，供 middleware 写入、交付层读取。"""

    _lock = Lock()
    _store: dict[tuple[str, str], dict[str, Any]] = {}
    _latest_run: dict[str, tuple[str, str]] = {}

    @classmethod
    def put(
        cls,
        session_id: str,
        snapshot: dict[str, Any],
        *,
        run_id: str = "",
    ) -> None:
        if not session_id:
            return
        key = (session_id, run_id or "")
        with cls._lock:
            cls._store[key] = dict(snapshot)
            cls._latest_run[session_id] = key

    @classmethod
    def peek(cls, session_id: str, *, run_id: str = "") -> dict[str, Any] | None:
        if not session_id:
            return None
        with cls._lock:
            if run_id:
                snapshot = cls._store.get((session_id, run_id))
                if snapshot is not None:
                    return dict(snapshot)
            latest_key = cls._latest_run.get(session_id)
            if latest_key is None:
                return None
            snapshot = cls._store.get(latest_key)
            return dict(snapshot) if snapshot is not None else None

    @classmethod
    def clear_run(cls, session_id: str, run_id: str) -> None:
        if not session_id or not run_id:
            return
        key = (session_id, run_id)
        with cls._lock:
            cls._store.pop(key, None)
            if cls._latest_run.get(session_id) == key:
                cls._latest_run.pop(session_id, None)

    @classmethod
    def clear(cls, session_id: str) -> None:
        if not session_id:
            return
        with cls._lock:
            for key in [key for key in cls._store if key[0] == session_id]:
                cls._store.pop(key, None)
            cls._latest_run.pop(session_id, None)

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._lock:
            cls._store.clear()
            cls._latest_run.clear()
