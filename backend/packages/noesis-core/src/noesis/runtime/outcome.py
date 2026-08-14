"""Stable, internal outcomes shared by the agent runtime and delivery layers.

The objects in this module deliberately contain no provider-specific exception
objects or user-facing diagnostic text.  Delivery code may map ``reason`` to a
wire event, while ``detail`` remains suitable for logs and traces only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from contextvars import ContextVar
from typing import Any, Mapping


class RuntimePhase(StrEnum):
    MODEL = "model"
    CONTEXT = "context"
    TOOL = "tool"
    GOVERNOR = "governor"


class RuntimeStatus(StrEnum):
    CONTINUE = "continue"
    RETRY = "retry"
    STOP = "stop"
    ERROR = "error"


class StopReason(StrEnum):
    COMPLETED = "completed"
    RETRYABLE_ERROR = "retryable_error"
    LENGTH_STOP = "length_stop"
    SAFETY_STOP = "safety_stop"
    CONTEXT_EXHAUSTED = "context_exhausted"
    PARTIAL_OUTPUT = "partial_output"
    EMPTY_AFTER_TOOLS = "empty_after_tools"
    TOOL_LOOP_LIMIT = "tool_loop_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    MODEL_CALL_LIMIT = "model_call_limit"
    SUBAGENT_CONCURRENCY_LIMIT = "subagent_concurrency_limit"
    SUBAGENT_TOTAL_LIMIT = "subagent_total_limit"
    SUBAGENT_DEPTH_LIMIT = "subagent_depth_limit"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RuntimeOutcome:
    """The one outcome vocabulary used by model, tool and governor owners."""

    phase: RuntimePhase | str
    status: RuntimeStatus | str
    reason: StopReason | str
    visible_output_started: bool = False
    side_effect_started: bool = False
    retryable: bool = False
    detail: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def wire_reason(self) -> str:
        return str(self.reason.value if isinstance(self.reason, StrEnum) else self.reason)

    def public_dict(self) -> dict[str, Any]:
        """Return the intentionally small, safe delivery representation."""
        return {
            "phase": str(self.phase.value if isinstance(self.phase, StrEnum) else self.phase),
            "status": str(self.status.value if isinstance(self.status, StrEnum) else self.status),
            "reason": self.wire_reason,
            "visible_output_started": self.visible_output_started,
            "side_effect_started": self.side_effect_started,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class ToolResultEnvelope:
    """Canonical tool result after typed failure handling and bounding."""

    tool_call_id: str
    tool_name: str
    status: str
    content: Any
    category: str | None = None
    outcome: str | None = None
    bounded_by: str = "none"
    original_size: int | None = None
    omitted_size: int | None = None
    timing_ms: float | None = None
    detail: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "category": self.category,
            "outcome": self.outcome,
            "bounded_by": self.bounded_by,
            "original_size": self.original_size,
            "omitted_size": self.omitted_size,
            "timing_ms": self.timing_ms,
        }


@dataclass(slots=True)
class GovernorState:
    """Mutable run-scoped counters; never store this on a middleware singleton."""

    run_id: str
    parent_run_id: str | None = None
    depth: int = 0
    model_calls: int = 0
    tool_calls_total: int = 0
    tool_calls_by_name: dict[str, int] = field(default_factory=dict)
    tool_window: list[str] = field(default_factory=list)
    active_subagents: int = 0
    subagents_total: int = 0
    stop_reason: str | None = None
    _started_at: float = field(default_factory=monotonic, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "depth": self.depth,
            "model_calls": self.model_calls,
            "tool_calls_total": self.tool_calls_total,
            "tool_calls_by_name": dict(self.tool_calls_by_name),
            "tool_window": list(self.tool_window),
            "active_subagents": self.active_subagents,
            "subagents_total": self.subagents_total,
            "stop_reason": self.stop_reason,
        }


def outcome(
    phase: RuntimePhase | str,
    status: RuntimeStatus | str,
    reason: StopReason | str,
    *,
    visible_output_started: bool = False,
    side_effect_started: bool = False,
    retryable: bool = False,
    detail: Mapping[str, Any] | None = None,
) -> RuntimeOutcome:
    return RuntimeOutcome(
        phase=phase,
        status=status,
        reason=reason,
        visible_output_started=visible_output_started,
        side_effect_started=side_effect_started,
        retryable=retryable,
        detail=dict(detail or {}),
    )


_CURRENT_OUTCOME: ContextVar[RuntimeOutcome | None] = ContextVar(
    "noesis_runtime_outcome", default=None
)
_CURRENT_TOOL_ENVELOPE: ContextVar[ToolResultEnvelope | None] = ContextVar(
    "noesis_tool_result_envelope", default=None
)


def current_runtime_outcome() -> RuntimeOutcome | None:
    return _CURRENT_OUTCOME.get()


def set_runtime_outcome(value: RuntimeOutcome | None) -> None:
    _CURRENT_OUTCOME.set(value)


def current_tool_result_envelope() -> ToolResultEnvelope | None:
    return _CURRENT_TOOL_ENVELOPE.get()


def set_tool_result_envelope(value: ToolResultEnvelope | None) -> None:
    _CURRENT_TOOL_ENVELOPE.set(value)
