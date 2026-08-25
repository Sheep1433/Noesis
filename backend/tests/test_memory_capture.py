from __future__ import annotations

from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.services.memory.capture import MemoryCaptureService, has_stable_work


def test_stable_work_uses_visible_conclusion_terminal_tool_or_artifact() -> None:
    assert has_stable_work({"parts": [{"type": "text", "content": "Validated result"}]})
    assert has_stable_work({"parts": [{"type": "tool", "state": "error", "error": "timeout"}]})
    assert has_stable_work({"parts": [{"type": "artifact", "digest": "abc"}]})
    assert not has_stable_work({"parts": [{"type": "reasoning", "content": "private"}]})
    assert not has_stable_work({"parts": [{"type": "retrieval", "results": [{"id": "old-memory"}]}]})
    assert not has_stable_work({"parts": [{"type": "tool", "state": "running", "output": "partial"}]})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "kind", "origin", "enabled", "expected"),
    [
        ("completed", "root", "web", True, True),
        ("partial", "root", "web", True, True),
        ("error", "root", "web", True, True),
        ("interrupted", "root", "web", True, True),
        ("hitl_pending", "root", "web", True, False),
        ("completed", "subagent", "web", True, False),
        ("completed", "root", "memory", True, False),
        ("completed", "root", "web", False, False),
    ],
)
async def test_terminal_capture_gate_is_not_failure_dependent(
    monkeypatch, status: str, kind: str, origin: str, enabled: bool, expected: bool
) -> None:
    enqueue = AsyncMock(return_value=True)

    class Repository:
        def __init__(self, _db):
            pass

        async def capture_context(self, _run_id):
            return SimpleNamespace(
                run_id="run-1",
                user_id="user-1",
                session_id="session-1",
                session_kind=kind,
                qa_type="SUPER_AGENT_QA",
                origin=origin,
                status=status,
            )

        enqueue_capture_job = enqueue

    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            return enabled

    monkeypatch.setattr("noesis.services.memory.capture.MachineMemoryRepository", Repository)
    monkeypatch.setattr("noesis.services.memory.capture.MemoryPreferenceRepository", Preference)

    created = await MemoryCaptureService.enqueue_for_terminal(
        SimpleNamespace(),
        run_id="run-1",
        content={"parts": [{"type": "text", "content": "Useful conclusion"}]},
    )

    assert created is expected
    assert enqueue.await_count == int(expected)


@pytest.mark.asyncio
async def test_interrupted_without_stable_work_does_not_capture(monkeypatch) -> None:
    repository = SimpleNamespace(
        capture_context=AsyncMock(return_value=SimpleNamespace(
            run_id="run-1",
            user_id="user-1",
            session_id="session-1",
            session_kind="root",
            qa_type="SUPER_AGENT_QA",
            origin="web",
            status="interrupted",
        )),
        enqueue_capture_job=AsyncMock(),
    )
    monkeypatch.setattr(
        "noesis.services.memory.capture.MachineMemoryRepository", lambda _db: repository
    )

    assert not await MemoryCaptureService.enqueue_for_terminal(
        SimpleNamespace(), run_id="run-1", content={"parts": []}
    )
    repository.enqueue_capture_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_recalled_bulletin_is_persisted_as_private_run_context(monkeypatch) -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    db.commit = AsyncMock()

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr(
        "noesis.services.memory.capture.pg_manager.get_async_session_context",
        context,
    )

    await MemoryCaptureService.record_recalled_bulletin(
        run_id="run-1",
        user_id="user-1",
        memory_ids=("memory-2", "memory-1", "memory-1"),
        bulletin_hash="a" * 64,
        degraded=False,
        source_snapshot_digest="b" * 64,
    )

    statement = db.execute.await_args.args[0]
    assert statement.compile().params["memory_context"]["memory_ids"] == [
        "memory-1",
        "memory-2",
    ]
    assert statement.compile().params["memory_context"]["degraded"] is False
    assert statement.compile().params["memory_context"]["source_snapshot_digest"] == "b" * 64
    db.commit.assert_awaited_once()
