"""Refresh DeepAgents Skills when the cross-turn source revision changes."""

from __future__ import annotations

from typing import Any, NotRequired

from deepagents.middleware.skills import SkillsMiddleware, SkillsState, SkillsStateUpdate
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from typing_extensions import Annotated

from noesis.agents.skills.revision import get_user_skills_revision


class RefreshingSkillsState(SkillsState):
    skills_revision: NotRequired[Annotated[str, PrivateStateAttr]]


class RefreshingSkillsMiddleware(SkillsMiddleware):
    """Use DeepAgents parsing while pinning one revision for the whole run."""

    state_schema = RefreshingSkillsState

    def __init__(self, *args: Any, user_id: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._user_id = str(user_id)

    def _prepare(self, state: RefreshingSkillsState) -> tuple[str, RefreshingSkillsState]:
        revision = get_user_skills_revision(self._user_id)
        if state.get("skills_revision") == revision and "skills_metadata" in state:
            return revision, dict(state)
        clean = dict(state)
        clean.pop("skills_metadata", None)
        clean.pop("skills_load_errors", None)
        return revision, clean  # type: ignore[return-value]

    def before_agent(
        self,
        state: RefreshingSkillsState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> SkillsStateUpdate | dict[str, Any] | None:
        revision, prepared = self._prepare(state)
        if state.get("skills_revision") == revision and "skills_metadata" in state:
            return None
        update = super().before_agent(prepared, runtime, config) or {}
        return {
            **update,
            "skills_load_errors": update.get("skills_load_errors", []),
            "skills_revision": revision,
        }

    async def abefore_agent(
        self,
        state: RefreshingSkillsState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> SkillsStateUpdate | dict[str, Any] | None:
        revision, prepared = self._prepare(state)
        if state.get("skills_revision") == revision and "skills_metadata" in state:
            return None
        update = await super().abefore_agent(prepared, runtime, config) or {}
        return {
            **update,
            "skills_load_errors": update.get("skills_load_errors", []),
            "skills_revision": revision,
        }
