"""Resolve the LangGraph thread identity consistently across middleware hooks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def resolve_runtime_thread_id(runtime: Any) -> str:
    """Prefer LangGraph execution metadata, with context as a compatibility fallback."""
    if runtime is None:
        return ""

    execution_info = getattr(runtime, "execution_info", None)
    thread_id = getattr(execution_info, "thread_id", None)
    if thread_id:
        return str(thread_id)

    context = getattr(runtime, "context", None)
    if isinstance(context, Mapping):
        thread_id = context.get("thread_id")
    else:
        thread_id = vars(context).get("thread_id") if hasattr(context, "__dict__") else None
    return str(thread_id) if thread_id else ""
