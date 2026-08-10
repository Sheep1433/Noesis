import asyncio
from types import SimpleNamespace

import pytest

from evals.agent.runtime import AgentEventCollector, collect_agent_events


def test_agent_event_collector_builds_common_manifest():
    collector = AgentEventCollector()
    collector.consume({"event": "on_tool_start", "name": "search_knowledge_base", "run_id": "1"})
    collector.consume(
        {
            "event": "on_tool_end",
            "name": "search_knowledge_base",
            "run_id": "1",
            "data": {"output": SimpleNamespace(content='{"hits": []}')},
        }
    )
    collector.consume(
        {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="answer")}}
    )
    collector.consume(
        {
            "event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(usage_metadata={"input_tokens": 3, "output_tokens": 2})},
        }
    )
    collector.consume({"type": "__tw_finish__", "finish_reason": "stop"})

    manifest = collector.result(
        run_id="run-1", suite="test", subject="agent", latency_ms=10
    ).to_manifest()
    assert manifest["schema_version"] == "noesis-eval-run/v1"
    assert manifest["completed"] is True
    assert manifest["final_text"] == "answer"
    assert manifest["tool_stats"] == {"search_knowledge_base": 1}
    assert manifest["tool_outputs"][0]["output"] == '{"hits": []}'
    assert manifest["input_tokens"] == 3
    assert manifest["output_tokens"] == 2


def test_agent_event_collector_error_cannot_be_overwritten_by_finish():
    collector = AgentEventCollector()
    collector.consume({"type": "__tw_error__", "content": "broken"})
    collector.consume({"type": "__tw_finish__", "finish_reason": "stop"})

    assert collector.completed is False
    assert collector.error == "broken"


@pytest.mark.asyncio
async def test_event_collection_records_timeout_when_stream_swallows_cancellation():
    cancelled = False

    async def stream():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            yield {"type": "abort", "finish_reason": "stop"}

    async def cancel():
        nonlocal cancelled
        cancelled = True

    collector = AgentEventCollector()
    await collect_agent_events(stream(), collector, timeout_seconds=0, cancel=cancel)

    assert cancelled is True
    assert collector.completed is False
    assert collector.error == "timeout after 0s"


@pytest.mark.asyncio
async def test_event_collection_cleans_up_when_cancel_callback_fails():
    stream_closed = asyncio.Event()

    async def stream():
        try:
            await asyncio.sleep(60)
            yield {}
        finally:
            stream_closed.set()

    async def cancel():
        raise RuntimeError("cancel failed")

    collector = AgentEventCollector()
    await collect_agent_events(stream(), collector, timeout_seconds=0, cancel=cancel)

    assert stream_closed.is_set()
    assert collector.error == "timeout after 0s; cancellation failed: cancel failed"


@pytest.mark.asyncio
async def test_event_collection_cleans_up_when_caller_is_cancelled():
    stream_closed = asyncio.Event()

    async def stream():
        try:
            await asyncio.sleep(60)
            yield {}
        finally:
            stream_closed.set()

    task = asyncio.create_task(
        collect_agent_events(
            stream(),
            AgentEventCollector(),
            timeout_seconds=60,
            cancel=lambda: asyncio.sleep(0),
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream_closed.is_set()
