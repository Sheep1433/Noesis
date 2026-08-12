"""无 SseDelivery 时 PersistSink 仍可得出终态决策。"""
from __future__ import annotations

from noesis.chat.delivery.events import RunCompleted, WireFrame
from noesis.services.persist_sink import PersistSink


def test_persist_sink_without_sse_subscriber() -> None:
    sink = PersistSink()
    sink.on_event(WireFrame(event="text-delta", data={"type": "text-delta", "delta": "x"}))
    d = sink.on_event(RunCompleted(finish_reason="stop"))
    assert d is not None
    assert d.kind == "completed"
    assert sink.final_decision().kind == "completed"


def test_checkpoint_is_throttled_but_semantic_boundary_is_immediate() -> None:
    sink = PersistSink(checkpoint_interval_seconds=2.0)
    sink._last_checkpoint_at = 10.0
    delta = WireFrame(event="text-delta", data={"type": "text-delta", "delta": "x"})
    tool_end = WireFrame(event="tool-output-available", data={"type": "tool-output-available"})

    assert sink.should_checkpoint(delta, now=10.5) is False
    assert sink.should_checkpoint(delta, now=12.0) is True
    assert sink.should_checkpoint(tool_end, now=12.1) is True


def test_long_text_checkpoint_count_is_time_bounded_not_token_bounded() -> None:
    sink = PersistSink(checkpoint_interval_seconds=2.0)
    sink._last_checkpoint_at = 0.0
    delta = WireFrame(event="text-delta", data={"type": "text-delta", "delta": "x"})

    checkpoint_count = sum(
        sink.should_checkpoint(delta, now=index / 1000)
        for index in range(10_000)
    )

    # 10 秒、每毫秒一个 delta，只产生约 4 次正文检查点，不是 10,000 次 UPDATE。
    assert checkpoint_count == 4
