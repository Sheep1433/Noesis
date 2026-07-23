"""无 SseDelivery 时 PersistSink 仍可得出终态决策。"""
from __future__ import annotations

from domain.chat.delivery.events import RunCompleted, WireFrame
from domain.chat.delivery.persist_sink import PersistSink


def test_persist_sink_without_sse_subscriber() -> None:
    sink = PersistSink()
    sink.on_event(WireFrame(event="text-delta", data={"type": "text-delta", "delta": "x"}))
    d = sink.on_event(RunCompleted(finish_reason="stop"))
    assert d is not None
    assert d.kind == "completed"
    assert sink.final_decision().kind == "completed"
