from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun

from noesis.middlewares import model_retry_middleware
from noesis.middlewares.model_retry_middleware import ModelRetryMiddleware
from noesis.runtime.model_attempt import (
    ModelAttemptCallback,
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
async def test_incomplete_chunked_model_stream_retries_before_visible_output(
    monkeypatch,
) -> None:
    monkeypatch.setattr(model_retry_middleware, "adispatch_custom_event", AsyncMock())
    middleware = ModelRetryMiddleware(max_retries=1, base_delay_seconds=0)
    tracker = ModelAttemptTracker()
    token = bind_model_attempt_tracker(tracker)
    handler = AsyncMock(
        side_effect=[
            httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body "
                "(incomplete chunked read)"
            ),
            "ok",
        ]
    )
    try:
        result = await middleware.awrap_model_call(object(), handler)
    finally:
        reset_model_attempt_tracker(token)

    assert result == "ok"
    assert handler.await_count == 2


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


@pytest.mark.asyncio
async def test_async_callback_manager_invokes_sync_attempt_hooks() -> None:
    tracker = ModelAttemptTracker()
    callback = ModelAttemptCallback(tracker)
    manager = AsyncCallbackManagerForLLMRun(
        run_id=uuid4(),
        handlers=[callback],
        inheritable_handlers=[],
        parent_run_id=None,
    )

    await manager.on_llm_new_token("token")

    assert tracker.visible_output_started is True


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
