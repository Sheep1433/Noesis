"""Noesis-only capability adapter contracts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from noesis.middlewares.capabilities.versioned_skills_middleware import VersionedSkillsMiddleware


def _middleware() -> VersionedSkillsMiddleware:
    return VersionedSkillsMiddleware(
        backend=MagicMock(),
        sources=["/skills/"],
        user_id="user-1",
    )


def test_versioned_skills_keeps_checkpoint_cache_when_revision_matches() -> None:
    state = {"skills_revision": "r1", "skills_metadata": [{"name": "existing"}]}
    with patch(
        "noesis.middlewares.capabilities.versioned_skills_middleware.get_user_skills_revision",
        return_value="r1",
    ):
        assert _middleware().before_agent(state, MagicMock(), {}) is None


def test_versioned_skills_reloads_via_deepagents_when_revision_changes() -> None:
    state = {"skills_revision": "r1", "skills_metadata": [{"name": "stale"}]}
    with (
        patch(
            "noesis.middlewares.capabilities.versioned_skills_middleware.get_user_skills_revision",
            return_value="r2",
        ),
        patch(
            "deepagents.middleware.skills.SkillsMiddleware.before_agent",
            return_value={"skills_metadata": [{"name": "fresh"}]},
        ) as parent_load,
    ):
        update = _middleware().before_agent(state, MagicMock(), {})

    assert update == {"skills_metadata": [{"name": "fresh"}], "skills_revision": "r2"}
    assert "skills_metadata" not in parent_load.call_args.args[0]
