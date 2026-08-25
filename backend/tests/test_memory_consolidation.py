from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from noesis.schemas.memory import MemorySourceSpan, RunSnapshotPayload, ValidatedMemoryCandidate
from noesis.services.memory.consolidation import (
    MemoryConsolidationService,
    decide_operation,
    identity_lock_key,
    initial_status,
)
from noesis.storage.postgres.models.memory import TMemoryItem


def _snapshot(*, scope: str = "profile:SUPER_AGENT_QA|project:git-origin:abc"):
    return RunSnapshotPayload(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        scope_key=scope,
        source_watermark=1,
        spans=[
            MemorySourceSpan(
                id="span-user",
                source_ref="message:user",
                kind="user_correction",
                provenance="user",
                effective_provenance="user",
                text="Use one switch.",
                digest="a" * 64,
            ),
            MemorySourceSpan(
                id="span-validation",
                source_ref="tool:test",
                kind="validation",
                provenance="tool_internal",
                effective_provenance="tool_internal",
                text="Tests passed.",
                digest="b" * 64,
            ),
        ],
        content_digest="c" * 64,
        token_estimate=20,
    )


def _candidate(*, digest: str = "d", relation=None, provenance="user"):
    return ValidatedMemoryCandidate(
        memory_type="decision",
        subject="Memory switch",
        subject_key="e" * 64,
        statement="Use one user-controlled switch.",
        evidence_refs=["span-user", "span-validation"],
        effective_provenance=provenance,
        confidence_reason="User corrected the design and tests passed.",
        proposed_relation=relation,
        content_digest=digest * 64,
        chunk_ids=["f" * 64],
    )


def _current(*, status="active", digest="x"):
    return TMemoryItem(
        id="item-current",
        user_id="user-1",
        scope_key="profile:SUPER_AGENT_QA|project:git-origin:abc",
        memory_type="decision",
        subject="Memory switch",
        subject_key="e" * 64,
        statement="Old statement.",
        applicability="",
        content_digest=digest * 64,
        effective_provenance="user",
        status=status,
        version=1,
    )


def test_activation_requires_project_scope_and_trusted_evidence() -> None:
    assert initial_status(_candidate(), _snapshot()) == "active"
    assert initial_status(
        _candidate(), _snapshot(scope="profile:SUPER_AGENT_QA|project:global")
    ) == "candidate"
    assert initial_status(_candidate(provenance="tool_external"), _snapshot()) == "candidate"

    gotcha = _candidate()
    gotcha.memory_type = "gotcha"
    gotcha.evidence_refs = ["span-validation"]
    assert initial_status(gotcha, _snapshot()) == "active"

    outcome_only = _snapshot()
    outcome_only.spans[1].kind = "tool_outcome"
    outcome_only.spans[1].metadata = {"state": "succeeded", "exit_code": 0}
    assert initial_status(gotcha, outcome_only) == "candidate"
    outcome_only.spans[1].metadata = {"state": "failed", "exit_code": 1}
    assert initial_status(gotcha, outcome_only) == "active"


def test_operation_rules_prioritize_user_state_and_evidence() -> None:
    snapshot = _snapshot()
    assert decide_operation(None, _candidate(), snapshot) == "ADD"
    assert decide_operation(_current(status="disabled"), _candidate(), snapshot) == "NOOP"
    assert decide_operation(_current(digest="d"), _candidate(digest="d"), snapshot) == "REINFORCE"
    assert decide_operation(_current(), _candidate(relation="contradicts"), snapshot) == "CONTRADICT"
    assert decide_operation(_current(), _candidate(), snapshot) == "SUPERSEDE"
    user_revision = _current()
    user_revision.user_revision = True
    candidate_without_correction = _candidate()
    candidate_without_correction.evidence_refs = ["span-validation"]
    assert decide_operation(user_revision, candidate_without_correction, snapshot) == "NOOP"
    no_correction = _snapshot()
    no_correction.spans[0].kind = "user_goal"
    additive = _candidate()
    additive.evidence_refs = ["span-validation"]
    current = _current()
    current.statement = "Use one user-controlled switch."
    additive.statement = "Use one user-controlled switch."
    assert decide_operation(current, additive, no_correction) == "UPDATE"
    additive.statement = "Use two unrelated controls."
    assert decide_operation(current, additive, no_correction) == "CONTRADICT"
    assert identity_lock_key(
        user_id="user-1",
        scope_key=snapshot.scope_key,
        memory_type="decision",
        subject_key="e" * 64,
    ) == identity_lock_key(
        user_id="user-1",
        scope_key=snapshot.scope_key,
        memory_type="decision",
        subject_key="e" * 64,
    )


@pytest.mark.asyncio
async def test_add_creates_item_evidence_and_both_desired_state_events() -> None:
    added_items = []
    evidence = []
    events = []

    class Repository:
        def add_item(self, item):
            item.id = "item-new"
            added_items.append(item)

        async def add_evidence_if_missing(self, item):
            evidence.append(item)
            return True

        def add_desired_state_events(self, item):
            events.extend([(item.id, "workspace"), (item.id, "index")])

        def add_relation(self, _relation):
            raise AssertionError("ADD does not create a relation")

    db = SimpleNamespace(flush=AsyncMock())
    item = await MemoryConsolidationService._apply(
        db,
        repository=Repository(),
        current=None,
        candidate=_candidate(),
        snapshot_id="snapshot-1",
        snapshot=_snapshot(),
        operation="ADD",
    )

    assert item.status == "active"
    assert len(added_items) == 1
    assert len(evidence) == 2
    assert events == [("item-new", "workspace"), ("item-new", "index")]


@pytest.mark.asyncio
async def test_disabled_item_is_not_automatically_revived() -> None:
    current = _current(status="disabled")
    repository = SimpleNamespace(
        add_evidence_if_missing=AsyncMock(),
        add_desired_state_events=AsyncMock(),
    )
    item = await MemoryConsolidationService._apply(
        SimpleNamespace(flush=AsyncMock()),
        repository=repository,
        current=current,
        candidate=_candidate(),
        snapshot_id="snapshot-1",
        snapshot=_snapshot(),
        operation="NOOP",
    )
    assert item is current
    assert item.status == "disabled"
    repository.add_evidence_if_missing.assert_not_awaited()


def test_recalled_item_with_only_compaction_evidence_is_not_reinforced() -> None:
    snapshot = RunSnapshotPayload(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        scope_key="profile:SUPER_AGENT_QA|project:git-origin:abc",
        source_watermark=1,
        spans=[
            MemorySourceSpan(
                id="span-compaction",
                source_ref="chunk:compaction:run-1",
                kind="compaction",
                provenance="assistant_derived",
                effective_provenance="assistant_derived",
                text="Summary restating a recalled memory.",
                digest="9" * 64,
            ),
            MemorySourceSpan(
                id="span-user",
                source_ref="message:user",
                kind="user_correction",
                provenance="user",
                effective_provenance="user",
                text="Use one switch.",
                digest="a" * 64,
            ),
        ],
        recalled_memory_ids=["item-current"],
        content_digest="c" * 64,
        token_estimate=20,
    )
    compaction_only = _candidate(digest="x")
    compaction_only.evidence_refs = ["span-compaction"]
    assert decide_operation(_current(digest="x"), compaction_only, snapshot) == "NOOP"

    independent = _candidate(digest="x")
    independent.evidence_refs = ["span-user"]
    assert decide_operation(_current(digest="x"), independent, snapshot) == "REINFORCE"
