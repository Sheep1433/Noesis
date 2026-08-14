"""Refresh DeepAgents memory at each run boundary."""

from __future__ import annotations

from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.memory import MemoryState, MemoryStateUpdate
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime


class RefreshingMemoryMiddleware(MemoryMiddleware):
    """Keep memory fixed within a run and refresh it across user turns."""

    @staticmethod
    def _without_cached_memory(state: MemoryState) -> MemoryState:
        prepared = dict(state)
        prepared.pop("memory_contents", None)
        return prepared  # type: ignore[return-value]

    def before_agent(
        self,
        state: MemoryState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> MemoryStateUpdate | None:
        return super().before_agent(self._without_cached_memory(state), runtime, config)

    async def abefore_agent(
        self,
        state: MemoryState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> MemoryStateUpdate | None:
        return await super().abefore_agent(self._without_cached_memory(state), runtime, config)
