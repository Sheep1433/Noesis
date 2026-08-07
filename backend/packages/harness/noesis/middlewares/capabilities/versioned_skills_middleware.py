"""DeepAgents Skills with a persistent, opaque source revision."""

from __future__ import annotations

from typing import Any, NotRequired

from deepagents.middleware.skills import SkillsMiddleware, SkillsState
from langchain.agents.middleware.types import PrivateStateAttr
from typing_extensions import Annotated

from noesis.runtime.context_provenance import (
    estimate_source_tokens,
    get_or_create_context_provenance,
)
from noesis.skills.revision import get_user_skills_revision


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

    def _record_skills_provenance(self, update: Any) -> None:
        """Tag the skills source once per Agent invocation based on loaded metadata.

        Skills metadata is loaded in ``before_agent`` and formatted into the
        system prompt lazily at model-call time. We estimate the metadata
        (the source of the skills section) here, consistent with the
        run-boundary load contract.
        """
        if not isinstance(update, dict):
            return
        metadata = update.get("skills_metadata")
        if not metadata:
            return
        tokens = estimate_source_tokens(metadata)
        if tokens > 0:
            get_or_create_context_provenance().add("skills", tokens)

    def before_agent(self, state, runtime, config=None):  # type: ignore[override]
        revision, prepared = self._prepare(state)
        if state.get("skills_revision") == revision and "skills_metadata" in state:
            return None
        update = super().before_agent(prepared, runtime, config) or {}
        self._record_skills_provenance(update)
        return {**update, "skills_revision": revision}

    async def abefore_agent(self, state, runtime, config=None):  # type: ignore[override]
        revision, prepared = self._prepare(state)
        if state.get("skills_revision") == revision and "skills_metadata" in state:
            return None
        update = await super().abefore_agent(prepared, runtime, config) or {}
        self._record_skills_provenance(update)
        return {**update, "skills_revision": revision}
