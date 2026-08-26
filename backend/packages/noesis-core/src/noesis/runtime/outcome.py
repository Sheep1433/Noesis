"""Stable stop reason vocabulary shared by the agent runtime and delivery layers.

Delivery code may map a reason string to a wire event. Values are the canonical
single source of truth for finish reasons.
"""

from __future__ import annotations

from enum import StrEnum


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
