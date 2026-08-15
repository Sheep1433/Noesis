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
    "StopReason": ("noesis.runtime.outcome", "StopReason"),
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
