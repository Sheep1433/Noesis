"""按 session 保存会话级 LLM 统计（步数/轮数/耗时/token）。

主 Agent 与 subagent 各自的 SessionStatsMiddleware 实例写同一份 session 级
状态，避免多实例各发各的 stats-update 在前端相互覆盖、消耗总量不完整。
模式参考 ContextMetricsRegistry：类级存储 + 锁，middleware 写入、发射层读取。
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any


class SessionStatsRegistry:
    """会话级 LLM 统计累计：middleware 累加，stats-update 组装时快照读取。

    累计语义跨 run 持续（会话统计条显示整个会话累计），故按最久未活跃
    淘汰，防止长期运行的进程无限积累已结束会话的状态。
    """

    _MAX_SESSIONS = 512

    _lock = Lock()
    _store: dict[str, dict[str, float]] = {}
    _last_active: dict[str, float] = {}

    _STATS_FIELDS = (
        "turns",
        "steps",
        "llm_ms",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    )

    @classmethod
    def _ensure(cls, session_id: str) -> dict[str, float]:
        stats = cls._store.get(session_id)
        if stats is None:
            stats = {field: 0.0 for field in cls._STATS_FIELDS}
            cls._store[session_id] = stats
        return stats

    @classmethod
    def add(cls, session_id: str, delta: dict[str, Any]) -> dict[str, float]:
        """累计一组增量（如一次模型调用的 steps=1/llm_ms/token），返回累计快照。"""
        if not session_id:
            return {}
        with cls._lock:
            stats = cls._ensure(session_id)
            for field in cls._STATS_FIELDS:
                value = delta.get(field)
                if value is not None:
                    stats[field] += float(value)
            cls._last_active[session_id] = time.monotonic()
            cls._evict_locked()
            return dict(stats)

    @classmethod
    def _evict_locked(cls) -> None:
        """超过上限时按最久未活跃淘汰（调用方须持锁）。"""
        if len(cls._store) <= cls._MAX_SESSIONS:
            return
        victims = sorted(cls._last_active, key=cls._last_active.get)[: len(cls._store) - cls._MAX_SESSIONS]
        for session_id in victims:
            cls._store.pop(session_id, None)
            cls._last_active.pop(session_id, None)

    @classmethod
    def peek(cls, session_id: str) -> dict[str, float] | None:
        if not session_id:
            return None
        with cls._lock:
            stats = cls._store.get(session_id)
            return dict(stats) if stats is not None else None

    @classmethod
    def clear(cls, session_id: str) -> None:
        if not session_id:
            return
        with cls._lock:
            cls._store.pop(session_id, None)
            cls._last_active.pop(session_id, None)

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._lock:
            cls._store.clear()
            cls._last_active.clear()
