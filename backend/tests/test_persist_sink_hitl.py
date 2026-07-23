"""PersistSink HITL / 终态判定。"""
from __future__ import annotations

from domain.chat.delivery.events import HitlRequired, RunCompleted, RunPaused
from domain.chat.delivery.persist_sink import PersistSink


def test_hitl_pending_is_not_completed() -> None:
    sink = PersistSink()
    sink.on_event(
        HitlRequired(
            payload={
                "type": "hitl-required",
                "interrupt_id": "i1",
                "kind": "approval",
            }
        )
    )
    d = sink.on_event(
        RunPaused(reason="hitl_pending", finish_reason="hitl_pending", usage={})
    )
    assert d is not None
    assert d.kind == "hitl_pending"
    final = sink.final_decision()
    assert final.kind == "hitl_pending"
    assert final.finish_reason == "hitl_pending"


def test_completed_without_hitl() -> None:
    sink = PersistSink()
    d = sink.on_event(RunCompleted(finish_reason="stop", usage={"total_tokens": 1}))
    assert d is not None
    assert d.kind == "completed"
    assert sink.final_decision().kind == "completed"


def test_run_completed_hitl_pending_reason() -> None:
    sink = PersistSink()
    d = sink.on_event(RunCompleted(finish_reason="hitl_pending"))
    assert d is not None
    assert d.kind == "hitl_pending"
