"""Stable execution-time API shared by embedded, evaluation, and platform hosts."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "DEFAULT_RECURSION_LIMIT": ("noesis.runtime.stream", "DEFAULT_RECURSION_LIMIT"),
    "format_agent_stream_error": ("noesis.runtime.stream", "format_agent_stream_error"),
    "stream_agent_events": ("noesis.runtime.stream", "stream_agent_events"),
    "logger": ("noesis.runtime.logging", "logger"),
    "bind_langfuse": ("noesis.runtime.deps", "bind_langfuse"),
    "temporary_attachment_service": ("noesis.runtime.deps", "temporary_attachment_service"),
    "RuntimeOutcome": ("noesis.runtime.outcome", "RuntimeOutcome"),
    "RuntimePhase": ("noesis.runtime.outcome", "RuntimePhase"),
    "RuntimeStatus": ("noesis.runtime.outcome", "RuntimeStatus"),
    "StopReason": ("noesis.runtime.outcome", "StopReason"),
    "ToolResultEnvelope": ("noesis.runtime.outcome", "ToolResultEnvelope"),
    "GovernorState": ("noesis.runtime.outcome", "GovernorState"),
    "ContextSnapshot": ("noesis.runtime.context_snapshot", "ContextSnapshot"),
    "ContextProvenance": ("noesis.runtime.context_provenance", "ContextProvenance"),
    "current_context_provenance": ("noesis.runtime.context_provenance", "current_context_provenance"),
    "get_or_create_context_provenance": ("noesis.runtime.context_provenance", "get_or_create_context_provenance"),
    "reset_context_provenance": ("noesis.runtime.context_provenance", "reset_context_provenance"),
    "estimate_source_tokens": ("noesis.runtime.context_provenance", "estimate_source_tokens"),
    "RunGovernor": ("noesis.runtime.governor", "RunGovernor"),
    "GovernorLimits": ("noesis.runtime.governor", "GovernorLimits"),
    "current_runtime_outcome": ("noesis.runtime.outcome", "current_runtime_outcome"),
    "current_run_governor": ("noesis.runtime.governor", "current_run_governor"),
    "current_tool_result_envelope": ("noesis.runtime.outcome", "current_tool_result_envelope"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
