"""Noesis-only capability adapter contracts."""

from __future__ import annotations

from typing import get_args, get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware.types import PrivateStateAttr

from noesis.agents.middlewares.refreshing_memory_middleware import RefreshingMemoryMiddleware
from noesis.agents.middlewares.refreshing_skills_middleware import (
    RefreshingSkillsMiddleware,
    RefreshingSkillsState,
)


def _middleware() -> RefreshingSkillsMiddleware:
    return RefreshingSkillsMiddleware(
        backend=MagicMock(),
        sources=["/skills/"],
        user_id="user-1",
    )


def test_versioned_skills_keeps_checkpoint_cache_when_revision_matches() -> None:
    state = {"skills_revision": "r1", "skills_metadata": [{"name": "existing"}]}
    with patch(
        "noesis.agents.middlewares.refreshing_skills_middleware.get_user_skills_revision",
        return_value="r1",
    ):
        assert _middleware().before_agent(state, MagicMock(), {}) is None


def test_capability_adapters_inherit_deepagents_and_revision_is_private() -> None:
    assert issubclass(RefreshingSkillsMiddleware, SkillsMiddleware)
    assert issubclass(RefreshingMemoryMiddleware, MemoryMiddleware)
    hint = get_type_hints(RefreshingSkillsState, include_extras=True)["skills_revision"]
    assert PrivateStateAttr in get_args(get_args(hint)[0])


def test_versioned_skills_reloads_via_deepagents_when_revision_changes() -> None:
    state = {"skills_revision": "r1", "skills_metadata": [{"name": "stale"}]}
    with (
        patch(
            "noesis.agents.middlewares.refreshing_skills_middleware.get_user_skills_revision",
            return_value="r2",
        ),
        patch(
            "deepagents.middleware.skills.SkillsMiddleware.before_agent",
            return_value={"skills_metadata": [{"name": "fresh"}]},
        ) as parent_load,
    ):
        update = _middleware().before_agent(state, MagicMock(), {})

    assert update == {
        "skills_metadata": [{"name": "fresh"}],
        "skills_load_errors": [],
        "skills_revision": "r2",
    }
    assert "skills_metadata" not in parent_load.call_args.args[0]


def test_versioned_skills_clears_stale_load_errors_on_refresh() -> None:
    state = {
        "skills_revision": "r1",
        "skills_metadata": [{"name": "stale"}],
        "skills_load_errors": ["old failure"],
    }
    with (
        patch(
            "noesis.agents.middlewares.refreshing_skills_middleware.get_user_skills_revision",
            return_value="r2",
        ),
        patch(
            "deepagents.middleware.skills.SkillsMiddleware.before_agent",
            return_value={"skills_metadata": []},
        ) as parent_load,
    ):
        update = _middleware().before_agent(state, MagicMock(), {})

    assert update is not None
    assert update["skills_load_errors"] == []
    assert "skills_load_errors" not in parent_load.call_args.args[0]


@pytest.mark.asyncio
async def test_versioned_skills_async_refreshes_only_at_run_boundary() -> None:
    state = {"skills_revision": "r1", "skills_metadata": [{"name": "stale"}]}
    with (
        patch(
            "noesis.agents.middlewares.refreshing_skills_middleware.get_user_skills_revision",
            return_value="r2",
        ) as revision,
        patch(
            "deepagents.middleware.skills.SkillsMiddleware.abefore_agent",
            new=AsyncMock(return_value={"skills_metadata": [{"name": "fresh"}]}),
        ) as parent_load,
    ):
        update = await _middleware().abefore_agent(state, MagicMock(), {})

    assert update is not None
    assert update["skills_revision"] == "r2"
    revision.assert_called_once_with("user-1")
    parent_load.assert_awaited_once()
