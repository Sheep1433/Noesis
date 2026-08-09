"""Read-only runtime telemetry middleware."""

from __future__ import annotations

from collections.abc import Callable

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from noesis.runtime.context_snapshot import current_context_snapshot
from noesis.runtime.outcome import (
    current_runtime_outcome,
    current_tool_result_envelope,
    set_tool_result_envelope,
)


class RuntimeTelemetryMiddleware(AgentMiddleware[AgentState]):
    def __init__(self, *, sink: Callable[[str, object], None] | None = None, enabled: bool = True) -> None:
        super().__init__()
        self.sink = sink
        self.enabled = enabled

    def record(self, event: str, value: object) -> None:
        if self.enabled and self.sink is not None:
            self.sink(event, value)

    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        snapshot = current_context_snapshot()
        if snapshot is not None:
            self.record("runtime.context", snapshot)
        return None

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.after_model(state, runtime)

    def wrap_model_call(self, request, handler):
        result = handler(request)
        value = current_runtime_outcome()
        if value is not None:
            self.record("runtime.outcome", value)
        return result

    async def awrap_model_call(self, request, handler):
        result = await handler(request)
        value = current_runtime_outcome()
        if value is not None:
            self.record("runtime.outcome", value)
        return result

    def wrap_tool_call(self, request, handler):
        set_tool_result_envelope(None)
        result = handler(request)
        envelope = current_tool_result_envelope()
        if envelope is not None:
            self.record("runtime.tool", envelope)
        return result

    async def awrap_tool_call(self, request, handler):
        set_tool_result_envelope(None)
        result = await handler(request)
        envelope = current_tool_result_envelope()
        if envelope is not None:
            self.record("runtime.tool", envelope)
        return result
