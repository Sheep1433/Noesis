"""DeepAgents Skills with a persistent, opaque source revision."""

from __future__ import annotations

from typing import Any, NotRequired

from deepagents.middleware.skills import SkillsMiddleware, SkillsState
from langchain.agents.middleware.types import PrivateStateAttr
from typing_extensions import Annotated

from noesis.agents.skills.revision import get_user_skills_revision


class VersionedSkillsState(SkillsState):
    skills_revision: NotRequired[Annotated[str, PrivateStateAttr]]


class VersionedSkillsMiddleware(SkillsMiddleware):
    state_schema = VersionedSkillsState

    def __init__(self, *args: Any, user_id: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user_id = str(user_id)

    def _prepare(self, state: Any) -> tuple[str, dict]:
        revision = get_user_skills_revision(self.user_id)
        if state.get("skills_revision") == revision and "skills_metadata" in state:
            return revision, dict(state)
        clean = dict(state)
        clean.pop("skills_metadata", None)
        clean.pop("skills_load_errors", None)
        return revision, clean

    def before_agent(self, state, runtime, config=None):  # type: ignore[override]
        revision, prepared = self._prepare(state)
        if state.get("skills_revision") == revision and "skills_metadata" in state:
            return None
        update = super().before_agent(prepared, runtime, config) or {}
        return {**update, "skills_revision": revision}

    async def abefore_agent(self, state, runtime, config=None):  # type: ignore[override]
        revision, prepared = self._prepare(state)
        if state.get("skills_revision") == revision and "skills_metadata" in state:
            return None
        update = await super().abefore_agent(prepared, runtime, config) or {}
        return {**update, "skills_revision": revision}
