"""In-process store for context snapshots keyed by (session_id, run_id).

A session may host concurrent runs (parallel sub-agents, overlapping requests).
Snapshots are isolated by run_id so one run cannot overwrite another's context
view mid-stream. ``peek`` resolves the run-specific snapshot when a run_id is
supplied, falling back to the session's most-recent snapshot for legacy callers.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Optional


class ContextMetricsRegistry:
    """按 (session_id, run_id) 暂存上下文快照，供 SSE bridge 读取。

    - ``put`` 写入 (session_id, run_id) 槽位；run_id 缺省时退化为 session 级单值
      （兼容无 run_id 的旧调用路径）。
    - ``peek`` 优先返回指定 run 的快照；未命中时回退到该 session 最新快照，
      保证 SSE bridge 在 run_id 不可用时仍能展示上下文占用。
    - ``clear_run`` 在 run 终态时清理单 run 快照；``clear`` 清理整个 session。
    """

    _lock = Lock()
    # (session_id, run_id) -> snapshot；run_id 为 "" 时表示 session 级回退槽
    _store: dict[tuple[str, str], dict[str, Any]] = {}
    # session_id -> 最近写入的 (session_id, run_id)，供 peek 回退
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
        rid = run_id or ""
        with cls._lock:
            if rid:
                snap = cls._store.get((session_id, rid))
                if snap is not None:
                    return dict(snap)
            # 回退：session 最新快照（含 run_id 为 "" 的兼容槽）
            latest_key = cls._latest_run.get(session_id)
            if latest_key is not None:
                snap = cls._store.get(latest_key)
                if snap is not None:
                    return dict(snap)
            return None

    @classmethod
    def clear_run(cls, session_id: str, run_id: str) -> None:
        """run 终态时清理单 run 快照；不清理 session 内其他 run。"""
        if not session_id or not run_id:
            return
        key = (session_id, run_id)
        with cls._lock:
            cls._store.pop(key, None)
            if cls._latest_run.get(session_id) == key:
                cls._latest_run.pop(session_id, None)

    @classmethod
    def clear(cls, session_id: str) -> None:
        """清理整个 session 的所有 run 快照。"""
        if not session_id:
            return
        with cls._lock:
            keys_to_drop = [k for k in cls._store if k[0] == session_id]
            for k in keys_to_drop:
                cls._store.pop(k, None)
            cls._latest_run.pop(session_id, None)

    @classmethod
    def _reset_for_tests(cls) -> None:
        """测试专用：清空全部状态。"""
        with cls._lock:
            cls._store.clear()
            cls._latest_run.clear()
