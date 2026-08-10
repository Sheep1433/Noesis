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


def resolve_runtime_run_id(runtime: Any) -> str:
    """Resolve the LangGraph run_id for run-scoped isolation.

    A run corresponds to a single agent invocation within a thread. Concurrent
    runs in the same thread (e.g. parallel sub-agents or overlapping requests)
    MUST be isolated by run_id so registry state does not cross-contaminate.
    Returns "" when run_id is unavailable; callers SHOULD fall back to thread_id.
    """
    if runtime is None:
        return ""
    execution_info = getattr(runtime, "execution_info", None)
    run_id = getattr(execution_info, "run_id", None)
    if run_id:
        return str(run_id)
    return ""
