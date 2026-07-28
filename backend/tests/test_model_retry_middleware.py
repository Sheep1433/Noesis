from unittest.mock import AsyncMock

import pytest

from noesis.middlewares import model_retry_middleware
from noesis.middlewares.model_retry_middleware import ModelRetryMiddleware
from noesis.runtime.model_attempt import (
    ModelAttemptTracker,
    bind_model_attempt_tracker,
    reset_model_attempt_tracker,
)
from noesis_server.domain.chat.streaming.langgraph_sse import LangGraphSseBridge


@pytest.mark.asyncio
async def test_transient_model_failure_emits_retrying_then_running(monkeypatch) -> None:
    emitted = []

    async def capture(name, data):
        emitted.append((name, data))

    monkeypatch.setattr(model_retry_middleware, "adispatch_custom_event", capture)
    middleware = ModelRetryMiddleware(max_retries=2, base_delay_seconds=0)
    tracker = ModelAttemptTracker()
    token = bind_model_attempt_tracker(tracker)
    handler = AsyncMock(side_effect=[TimeoutError("slow"), "ok"])
    try:
        result = await middleware.awrap_model_call(object(), handler)
    finally:
        reset_model_attempt_tracker(token)

    assert result == "ok"
    assert handler.await_count == 2
    assert [item[1]["status"] for item in emitted] == ["retrying", "running"]
    assert emitted[0][1]["attempt_id"] == 2


@pytest.mark.asyncio
async def test_model_failure_does_not_retry_after_visible_output(monkeypatch) -> None:
    dispatch = AsyncMock()
    monkeypatch.setattr(model_retry_middleware, "adispatch_custom_event", dispatch)
    middleware = ModelRetryMiddleware(max_retries=2, base_delay_seconds=0)
    tracker = ModelAttemptTracker(visible_output_started=True)
    token = bind_model_attempt_tracker(tracker)
    handler = AsyncMock(side_effect=TimeoutError("late failure"))
    try:
        with pytest.raises(TimeoutError):
            await middleware.awrap_model_call(object(), handler)
    finally:
        reset_model_attempt_tracker(token)

    assert handler.await_count == 1
    dispatch.assert_not_awaited()


def test_retry_custom_event_becomes_run_status_frame() -> None:
    bridge = LangGraphSseBridge("session-1", assistant_message_id="message-1")
    lines = bridge.process_item(
        {
            "event": "on_custom_event",
            "name": "noesis_model_retry",
            "data": {
                "status": "retrying",
                "attempt_id": 2,
                "attempt": 1,
                "max_attempts": 2,
            },
        },
        None,
        {},
    )

    assert lines[0].startswith("event: run-status\n")
    assert '"attempt_id": 2' in lines[0]
