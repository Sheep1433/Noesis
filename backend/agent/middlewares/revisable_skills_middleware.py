"""可按用户 Skills revision 强制重扫的 SkillsMiddleware。"""

from __future__ import annotations

from typing import Any, NotRequired

from deepagents.middleware.skills import SkillsMiddleware, SkillsState
from langchain.agents.middleware.types import PrivateStateAttr
from typing_extensions import Annotated

from services.skill_fs_service import get_user_skills_revision


class SkillsRevisionState(SkillsState):
    skills_content_revision: NotRequired[Annotated[str, PrivateStateAttr]]


class RevisableSkillsMiddleware(SkillsMiddleware):
    """当用户个人 Skills revision 变化时，清除 skills_metadata 并重扫。"""

    state_schema = SkillsRevisionState

    def __init__(self, *args, user_id: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._user_id = user_id

    def before_agent(self, state, runtime, config: Any = None):  # type: ignore[override]
        rev = get_user_skills_revision(self._user_id)
        if state.get("skills_content_revision") == rev and "skills_metadata" in state:
            return None
        mutated = dict(state)
        mutated.pop("skills_metadata", None)
        mutated.pop("skills_load_errors", None)
        update = super().before_agent(mutated, runtime, config) or {}
        return {**update, "skills_content_revision": rev}

    async def abefore_agent(self, state, runtime, config: Any = None):  # type: ignore[override]
        rev = get_user_skills_revision(self._user_id)
        if state.get("skills_content_revision") == rev and "skills_metadata" in state:
            return None
        mutated = dict(state)
        mutated.pop("skills_metadata", None)
        mutated.pop("skills_load_errors", None)
        update = await super().abefore_agent(mutated, runtime, config) or {}
        return {**update, "skills_content_revision": rev}
