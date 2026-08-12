"""Unit contracts for ``SourceRefreshMiddleware`` (revision + invalidation)."""

from __future__ import annotations

from noesis.middleware.source_refresh_middleware import (
    SourceFingerprint,
    SourceRefreshMiddleware,
)


def _fp(skills="s1", memory="m1", tools="t1", att="a1", prompt="p1") -> SourceFingerprint:
    return SourceFingerprint(
        skills_hash=skills,
        memory_hash=memory,
        tool_catalog_hash=tools,
        attachments_hash=att,
        scene_prompt_hash=prompt,
    )


def test_revision_stable_for_equal_fingerprints() -> None:
    fp = _fp()
    assert fp.revision() == fp.revision()


def test_revision_changes_when_skills_change() -> None:
    assert _fp(skills="s1").revision() != _fp(skills="s2").revision()


def test_first_turn_invalidates_all_caches() -> None:
    state = {"messages": [], "skills_metadata": ["old"], "memory_contents": {"x": "y"}}
    mw = SourceRefreshMiddleware(source_provider=lambda: _fp())
    update = mw.before_agent(state, runtime=None)
    # first turn: prev is None → invalidate all keys
    assert "skills_metadata" not in state
    assert "memory_contents" not in state
    assert update is not None
    assert update["_source_revision"]["revision"] == _fp().revision()


def test_unchanged_revision_does_not_invalidate() -> None:
    state = {"messages": [], "skills_metadata": ["loaded"], "memory_contents": {"x": "y"}}
    mw = SourceRefreshMiddleware(source_provider=lambda: _fp())
    # first turn invalidates
    mw.before_agent(state, runtime=None)
    # simulate Skills reloading into state
    state["skills_metadata"] = ["loaded"]
    state["memory_contents"] = {"x": "y"}
    # second turn, same fingerprint → no invalidation
    state["_source_revision"] = {
        "revision": _fp().revision(),
        "fingerprint": {
            "skills_hash": "s1", "memory_hash": "m1", "tool_catalog_hash": "t1",
            "attachments_hash": "a1", "scene_prompt_hash": "p1",
        },
        "invalidated_keys": [],
    }
    update = mw.before_agent(state, runtime=None)
    assert "skills_metadata" in state  # not removed
    assert update is None


def test_skills_change_invalidates_only_skills_cache() -> None:
    mw = SourceRefreshMiddleware(source_provider=lambda: _fp())
    state = {"messages": []}
    mw.before_agent(state, runtime=None)  # first turn
    # set up loaded state + prior revision
    state["skills_metadata"] = ["loaded"]
    state["memory_contents"] = {"x": "y"}
    curr_fp = _fp(skills="s2")  # only skills changed
    state["_source_revision"] = {
        "revision": _fp().revision(),
        "fingerprint": {
            "skills_hash": "s1", "memory_hash": "m1", "tool_catalog_hash": "t1",
            "attachments_hash": "a1", "scene_prompt_hash": "p1",
        },
        "invalidated_keys": [],
    }
    mw2 = SourceRefreshMiddleware(source_provider=lambda: curr_fp)
    update = mw2.before_agent(state, runtime=None)
    assert "skills_metadata" not in state  # skills cache cleared
    assert "memory_contents" in state  # memory cache untouched
    assert update is not None
    assert update["_source_revision"]["revision"] == curr_fp.revision()


def test_no_provider_returns_none() -> None:
    mw = SourceRefreshMiddleware(source_provider=None)
    assert mw.before_agent({"messages": []}, runtime=None) is None


def test_within_run_stability_after_mid_run_write() -> None:
    # Once a revision is fixed for the run, a later before_agent with the same
    # fingerprint does not re-invalidate (so a mid-run memory write doesn't
    # shift the prompt).
    mw = SourceRefreshMiddleware(source_provider=lambda: _fp())
    state = {"messages": []}
    update = mw.before_agent(state, runtime=None)
    # framework applies the returned update
    assert update is not None
    state.update(update)
    state["skills_metadata"] = ["loaded"]
    # same fingerprint again → no invalidation
    assert mw.before_agent(state, runtime=None) is None
    assert "skills_metadata" in state


def test_invalidate_returns_removed_keys() -> None:
    mw = SourceRefreshMiddleware()
    state = {"skills_metadata": ["x"], "memory_contents": {"y": 1}}
    removed = mw.invalidate(state, ("skills_metadata", "memory_contents", "nonexistent"))
    assert "skills_metadata" not in state
    assert "memory_contents" not in state
    assert removed == ["skills_metadata", "memory_contents"]
