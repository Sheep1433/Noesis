"""Noesis core backend — agents, services, knowledge, and storage.

Platform Delivery (SSE / Persist / channels) lives outside this package.
Evals and services SHALL call into noesis; noesis MUST NOT import
``services``, ``domain``, ``models``, ``api``, or ``kb``.
Host-specific observability callbacks are bound through ``noesis.runtime.deps``.
"""

from __future__ import annotations

from typing import Any

__all__ = ["create_noesis_agent"]


def __getattr__(name: str) -> Any:
    if name == "create_noesis_agent":
        from noesis.factory import create_noesis_agent

        return create_noesis_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
