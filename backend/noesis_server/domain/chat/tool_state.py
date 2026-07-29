"""Tool part lifecycle shared by streaming, projections and persistence."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ToolState(StrEnum):
    RUNNING = "running"
    APPROVAL_PENDING = "approval_pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


TERMINAL_TOOL_STATES = frozenset(
    {
        ToolState.SUCCEEDED,
        ToolState.FAILED,
        ToolState.TIMED_OUT,
        ToolState.REJECTED,
        ToolState.CANCELLED,
    }
)


def is_terminal_tool_state(state: ToolState | str | None) -> bool:
    try:
        return ToolState(str(state)) in TERMINAL_TOOL_STATES
    except ValueError:
        return False


def can_transition_tool_state(current: ToolState | str, target: ToolState | str) -> bool:
    current_state = ToolState(str(current))
    target_state = ToolState(str(target))
    if current_state == target_state:
        return True
    if current_state in TERMINAL_TOOL_STATES:
        return False
    if current_state == ToolState.APPROVAL_PENDING:
        return target_state in {
            ToolState.RUNNING,
            ToolState.REJECTED,
            ToolState.CANCELLED,
            ToolState.FAILED,
            ToolState.TIMED_OUT,
        }
    return target_state != ToolState.APPROVAL_PENDING or current_state == ToolState.RUNNING


def derive_tool_state(
    *,
    status: str | None = None,
    outcome: str | None = None,
    error_category: str | None = None,
    timed_out: bool | None = None,
) -> ToolState:
    """Map invoke/process semantics to the user-facing authoritative lifecycle."""
    normalized_status = str(status or "").lower()
    normalized_outcome = str(outcome or "").lower()
    normalized_category = str(error_category or "").lower()
    if timed_out or normalized_outcome == "timed_out" or normalized_category in {
        "execution_timeout",
        "tool_timeout",
        "network_timeout",
        "timeout",
    }:
        return ToolState.TIMED_OUT
    if normalized_outcome in {"rejected", "user_rejected"}:
        return ToolState.REJECTED
    if normalized_outcome in {"cancelled", "canceled", "stopped"}:
        return ToolState.CANCELLED
    if normalized_category == "cancelled":
        return ToolState.CANCELLED
    if normalized_status in {"error", "failed"} or normalized_outcome in {
        "command_failed",
        "failed",
        "unknown",
    }:
        return ToolState.FAILED
    if normalized_status in {"running", "streaming"}:
        return ToolState.RUNNING
    return ToolState.SUCCEEDED


def extract_process_result(raw: Any) -> dict[str, Any]:
    """Read process metadata only from explicit mapping/object fields."""
    result: dict[str, Any] = {}
    for key in ("exit_code", "timed_out", "truncated", "outcome"):
        if isinstance(raw, dict) and key in raw:
            result[key] = raw[key]
        elif hasattr(raw, key):
            result[key] = getattr(raw, key)
    if "timed_out" in result:
        result["timed_out"] = bool(result["timed_out"])
    if result.get("timed_out"):
        result["outcome"] = "timed_out"
    elif "exit_code" in result:
        try:
            exit_code = int(result["exit_code"])
        except (TypeError, ValueError):
            result.pop("exit_code", None)
        else:
            result["exit_code"] = exit_code
            result["outcome"] = "ok" if exit_code == 0 else "command_failed"
    return result
