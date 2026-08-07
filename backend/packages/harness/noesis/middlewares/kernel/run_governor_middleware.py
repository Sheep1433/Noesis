"""LangChain hook adapter for the run-scoped Governor."""

from __future__ import annotations

from contextvars import ContextVar
from typing import NotRequired
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import PrivateStateAttr
from langgraph.runtime import Runtime
from typing_extensions import Annotated

from noesis.config.env import ModelConfig
from noesis.runtime.governor import GovernorLimits, RunGovernor, current_run_governor, governor_run_id, reset_run_governor, set_run_governor
from noesis.runtime.outcome import set_runtime_outcome


class RunGovernorState(AgentState):
    noesis_governor: NotRequired[Annotated[dict, PrivateStateAttr]]


def default_governor_limits() -> GovernorLimits:
    tool_limits_enabled = getattr(ModelConfig, "governor_tool_calls_enabled", True)
    return GovernorLimits(
        tool_calls_total=(
            getattr(ModelConfig, "governor_tool_calls_total", None)
            if tool_limits_enabled
            else None
        ),
        tool_calls_per_name=(
            getattr(ModelConfig, "governor_tool_calls_per_name", None)
            if tool_limits_enabled
            else None
        ),
        loop_window_size=getattr(ModelConfig, "governor_loop_window_size", 20),
        loop_hard_limit=getattr(ModelConfig, "governor_loop_hard_limit", None) if getattr(ModelConfig, "governor_loop_enabled", False) else None,
    )


class RunGovernorMiddleware(AgentMiddleware[RunGovernorState]):
    state_schema = RunGovernorState

    def __init__(self, *, limits: GovernorLimits | None = None, parent: RunGovernor | None = None) -> None:
        super().__init__()
        self.limits = limits or default_governor_limits()
        self.parent = parent
        self._owns_binding: ContextVar[bool] = ContextVar(
            f"noesis_governor_owner_{id(self)}", default=False
        )

    def _bind(self, state: RunGovernorState, runtime: Runtime | None) -> None:
        current = current_run_governor()
        if current is not None:
            self._owns_binding.set(False)
            return
        run_id = governor_run_id(runtime)
        snapshot = state.get("noesis_governor")
        if isinstance(snapshot, dict) and snapshot.get("run_id") == run_id:
            governor = RunGovernor.from_snapshot(
                snapshot,
                limits=self.limits,
                parent=self.parent,
            )
        else:
            governor = RunGovernor(run_id, limits=self.limits, parent=self.parent)
        set_run_governor(governor)
        self._owns_binding.set(True)

    @staticmethod
    def _snapshot() -> dict | None:
        governor = current_run_governor()
        return {"noesis_governor": governor.state.snapshot()} if governor is not None else None

    def before_agent(self, state: RunGovernorState, runtime: Runtime) -> dict | None:
        self._bind(state, runtime)
        return self._snapshot()

    async def abefore_agent(self, state: RunGovernorState, runtime: Runtime) -> dict | None:
        return self.before_agent(state, runtime)

    def before_model(self, state: RunGovernorState, runtime: Runtime) -> dict | None:
        governor = current_run_governor()
        if governor is None:
            self._bind(state, runtime)
            governor = current_run_governor()
        if governor is not None:
            stopped = governor.reserve_model()
            if stopped is not None:
                set_runtime_outcome(stopped)
        return self._snapshot()

    async def abefore_model(self, state: RunGovernorState, runtime: Runtime) -> dict | None:
        return self.before_model(state, runtime)

    def after_agent(self, state: RunGovernorState, runtime: Runtime) -> dict | None:
        update = self._snapshot()
        if self._owns_binding.get():
            reset_run_governor()
            self._owns_binding.set(False)
        return update

    async def aafter_agent(self, state: RunGovernorState, runtime: Runtime) -> dict | None:
        return self.after_agent(state, runtime)
