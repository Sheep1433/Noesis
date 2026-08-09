"""Run-scoped execution budgets for model, tool and sub-agent calls."""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator

from noesis.runtime.outcome import GovernorState, RuntimeOutcome, RuntimePhase, RuntimeStatus, StopReason, outcome
from noesis.runtime.thread_context import resolve_runtime_thread_id


@dataclass(frozen=True, slots=True)
class GovernorLimits:
    model_calls: int | None = None
    tool_calls_total: int | None = None
    tool_calls_per_name: int | None = None
    loop_window_size: int = 8
    loop_hard_limit: int | None = None
    max_active_subagents: int | None = None
    max_subagents_total: int | None = None
    max_depth: int | None = None
    token_budget: int | None = None


class RunGovernor:
    """Thread-safe budget owner shared by the main run and child runs."""

    def __init__(self, run_id: str, *, limits: GovernorLimits | None = None, parent: "RunGovernor | None" = None, depth: int = 0) -> None:
        self.limits = limits or GovernorLimits()
        self.state = GovernorState(
            run_id=run_id,
            parent_run_id=parent.state.run_id if parent else None,
            depth=depth,
        )
        self.parent = parent
        self._lock = threading.RLock()

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict,
        *,
        limits: GovernorLimits | None = None,
        parent: "RunGovernor | None" = None,
    ) -> "RunGovernor":
        governor = cls(
            str(snapshot.get("run_id") or "runtime"),
            limits=limits,
            parent=parent,
            depth=int(snapshot.get("depth") or 0),
        )
        governor.state.parent_run_id = snapshot.get("parent_run_id")
        governor.state.model_calls = int(snapshot.get("model_calls") or 0)
        governor.state.tool_calls_total = int(snapshot.get("tool_calls_total") or 0)
        governor.state.tool_calls_by_name = dict(snapshot.get("tool_calls_by_name") or {})
        governor.state.tool_window = list(snapshot.get("tool_window") or [])
        # Active work cannot survive a process/interruption boundary.
        governor.state.active_subagents = 0
        governor.state.subagents_total = int(snapshot.get("subagents_total") or 0)
        governor.state.stop_reason = snapshot.get("stop_reason")
        governor.state.actual_provider_tokens = snapshot.get("actual_provider_tokens")
        return governor

    def _stop(self, reason: StopReason) -> RuntimeOutcome:
        self.state.stop_reason = reason.value
        return outcome(RuntimePhase.GOVERNOR, RuntimeStatus.STOP, reason, detail=self.state.snapshot())

    def reserve_model(self) -> RuntimeOutcome | None:
        with self._lock:
            if self.state.stop_reason == StopReason.TOKEN_BUDGET.value:
                return self._stop(StopReason.TOKEN_BUDGET)
            limit = self.limits.model_calls
            if limit is not None and self.state.model_calls >= limit:
                return self._stop(StopReason.MODEL_CALL_LIMIT)
            if self.parent is not None:
                parent_result = self.parent.reserve_model()
                if parent_result is not None:
                    return parent_result
            self.state.model_calls += 1
            return None

    def reserve_tool(self, tool_name: str) -> RuntimeOutcome | None:
        name = str(tool_name or "unknown_tool")
        with self._lock:
            if self.limits.tool_calls_total is not None and self.state.tool_calls_total >= self.limits.tool_calls_total:
                return self._stop(StopReason.TOOL_CALL_LIMIT)
            if self.limits.tool_calls_per_name is not None and self.state.tool_calls_by_name.get(name, 0) >= self.limits.tool_calls_per_name:
                return self._stop(StopReason.TOOL_CALL_LIMIT)
            key = name
            self.state.tool_window.append(key)
            window = max(1, self.limits.loop_window_size)
            if len(self.state.tool_window) > window:
                del self.state.tool_window[:-window]
            loop_limit = self.limits.loop_hard_limit
            if loop_limit is not None and self.state.tool_window.count(key) >= loop_limit:
                return self._stop(StopReason.TOOL_LOOP_LIMIT)
            if self.parent is not None:
                parent_result = self.parent.reserve_tool(name)
                if parent_result is not None:
                    return parent_result
            self.state.tool_calls_total += 1
            self.state.tool_calls_by_name[name] = self.state.tool_calls_by_name.get(name, 0) + 1
            return None

    def reserve_subagent(self) -> RuntimeOutcome | None:
        with self._lock:
            if self.limits.max_depth is not None and self.state.depth + 1 > self.limits.max_depth:
                return self._stop(StopReason.SUBAGENT_DEPTH_LIMIT)
            if self.limits.max_active_subagents is not None and self.state.active_subagents >= self.limits.max_active_subagents:
                return self._stop(StopReason.SUBAGENT_CONCURRENCY_LIMIT)
            if self.limits.max_subagents_total is not None and self.state.subagents_total >= self.limits.max_subagents_total:
                return self._stop(StopReason.SUBAGENT_TOTAL_LIMIT)
            if self.parent is not None:
                parent_result = self.parent.reserve_subagent()
                if parent_result is not None:
                    return parent_result
            self.state.active_subagents += 1
            self.state.subagents_total += 1
            return None

    def release_subagent(self) -> None:
        with self._lock:
            self.state.active_subagents = max(0, self.state.active_subagents - 1)
            if self.parent is not None:
                self.parent.release_subagent()

    def child(self, run_id: str) -> "RunGovernor":
        return RunGovernor(run_id, limits=self.limits, parent=self, depth=self.state.depth + 1)

    def record_actual_tokens(self, total_tokens: int) -> None:
        """Enable token accounting only when a real provider usage value exists."""
        if total_tokens < 0:
            return
        with self._lock:
            self.state.actual_provider_tokens = (self.state.actual_provider_tokens or 0) + total_tokens
            if self.limits.token_budget is not None and self.state.actual_provider_tokens > self.limits.token_budget:
                self.state.stop_reason = StopReason.TOKEN_BUDGET.value


_CURRENT_GOVERNOR: ContextVar[RunGovernor | None] = ContextVar("noesis_run_governor", default=None)
_CURRENT_GOVERNOR_TOKEN: ContextVar[Token | None] = ContextVar("noesis_run_governor_token", default=None)


def current_run_governor() -> RunGovernor | None:
    return _CURRENT_GOVERNOR.get()


@contextmanager
def bind_run_governor(governor: RunGovernor) -> Iterator[RunGovernor]:
    token = _CURRENT_GOVERNOR.set(governor)
    try:
        yield governor
    finally:
        _CURRENT_GOVERNOR.reset(token)


def set_run_governor(governor: RunGovernor) -> Token:
    token = _CURRENT_GOVERNOR.set(governor)
    _CURRENT_GOVERNOR_TOKEN.set(token)
    return token


def reset_run_governor() -> None:
    token = _CURRENT_GOVERNOR_TOKEN.get()
    if token is not None:
        _CURRENT_GOVERNOR.reset(token)
        _CURRENT_GOVERNOR_TOKEN.set(None)


def governor_run_id(runtime: object | None, fallback: str = "") -> str:
    if runtime is not None:
        resolved = resolve_runtime_thread_id(runtime)
        if resolved:
            return resolved
    return fallback or f"run-{uuid.uuid4().hex}"
