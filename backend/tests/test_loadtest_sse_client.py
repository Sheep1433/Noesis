"""SSE 客户端解析单测。"""

from evals.loadtest.sse_client import consume_sse_stream


def _lines(*parts: str):
    for part in parts:
        yield from part.splitlines()


def test_consume_sse_stream_success() -> None:
    frames = (
        'event: text-delta\ndata: {"type":"text-delta","delta":"hello"}\n\n'
        'event: finish\ndata: {"type":"finish","finish_reason":"stop","usage":{}}\n\n'
        "data: [DONE]\n\n"
    )
    metrics = consume_sse_stream(_lines(frames))
    assert metrics.succeeded
    assert metrics.ttft_ms is not None
    assert metrics.finish_reason == "stop"
    assert metrics.event_counts["text-delta"] == 1


def test_consume_sse_stream_waits_for_done_after_finish() -> None:
    """收到 finish 后须继续读到 [DONE]，不可提前结束。"""
    frames = (
        'event: finish\ndata: {"type":"finish","finish_reason":"stop","usage":{}}\n\n'
        "data: [DONE]\n\n"
    )
    metrics = consume_sse_stream(_lines(frames))
    assert metrics.succeeded
    assert metrics.finish_reason == "stop"
    assert metrics.error_message is None


def test_consume_sse_stream_error_without_done() -> None:
    frames = 'event: error\ndata: {"type":"error","error":"boom"}\n\n'
    metrics = consume_sse_stream(_lines(frames))
    assert not metrics.succeeded
    assert metrics.error_message == "boom"
