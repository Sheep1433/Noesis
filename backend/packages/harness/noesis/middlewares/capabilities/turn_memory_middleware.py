"""Reload DeepAgents memory once at the start of each Agent invocation."""

from __future__ import annotations

from typing import Any

from deepagents.middleware.memory import MemoryMiddleware

from noesis.runtime.context_provenance import (
    estimate_source_tokens,
    get_or_create_context_provenance,
)


class TurnMemoryMiddleware(MemoryMiddleware):
    """Keep memory fixed within a run while refreshing it for each user turn."""

    @staticmethod
    def _without_cached_memory(state: Any) -> dict:
        prepared = dict(state)
        prepared.pop("memory_contents", None)
        return prepared

    @staticmethod
    def _record_memory_provenance(update: Any) -> None:
        """Tag the memory source once per Agent invocation.

        Memory is loaded in ``before_agent`` and fixed for the run, so provenance
        is recorded here (not per model call) — consistent with the run-boundary
        reload contract. We estimate the loaded ``memory_contents`` rather than
        diffing the system message, since the base class formats memory into the
        system prompt lazily at model-call time but the source content is already
        known here.
        """
        if not isinstance(update, dict):
            return
        contents = update.get("memory_contents")
        if not contents:
            return
        tokens = estimate_source_tokens(contents)
        if tokens > 0:
            get_or_create_context_provenance().add("memory", tokens)

    def before_agent(self, state, runtime, config=None):  # type: ignore[override]
        update = super().before_agent(self._without_cached_memory(state), runtime, config)
        self._record_memory_provenance(update)
        return update

    async def abefore_agent(self, state, runtime, config=None):  # type: ignore[override]
        update = await super().abefore_agent(self._without_cached_memory(state), runtime, config)
        self._record_memory_provenance(update)
        return update
