"""Reload DeepAgents memory once at the start of each Agent invocation."""

from __future__ import annotations

from typing import Any

from deepagents.middleware.memory import MemoryMiddleware


class TurnMemoryMiddleware(MemoryMiddleware):
    """Keep memory fixed within a run while refreshing it for each user turn."""

    @staticmethod
    def _without_cached_memory(state: Any) -> dict:
        prepared = dict(state)
        prepared.pop("memory_contents", None)
        return prepared

    def before_agent(self, state, runtime, config=None):  # type: ignore[override]
        return super().before_agent(self._without_cached_memory(state), runtime, config)

    async def abefore_agent(self, state, runtime, config=None):  # type: ignore[override]
        return await super().abefore_agent(self._without_cached_memory(state), runtime, config)
