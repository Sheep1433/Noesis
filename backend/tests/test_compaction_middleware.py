from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from noesis.agents.middlewares.compaction_middleware import (
    CompactionMiddleware,
    CompactionThresholds,
)


def _thresholds(auto_at: int = 50) -> CompactionThresholds:
    return CompactionThresholds(
        model_input_limit=auto_at + 210,
        summary_output_reserve=10,
        transient_request_buffer=200,
    )


def _request(messages, state=None, tools=()) -> ModelRequest:
    state = state if state is not None else {"messages": list(messages)}
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=list(messages),
        system_message=SystemMessage(content="system"),
        tools=list(tools),
        state=state,
    )


def _response() -> ModelResponse:
    return ModelResponse(result=[AIMessage(content="ok")])


def _messages(count: int = 20):
    return [HumanMessage(content=f"message {index} " + "x" * 20) for index in range(count)]


def _command_update(result) -> dict:
    assert isinstance(result, ExtendedModelResponse)
    assert result.command is not None
    return result.command.update


def test_threshold_accounts_for_system_and_tool_schemas() -> None:
    seen = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: len(messages) * 10,
        summarize=lambda messages: "summary",
        thresholds=_thresholds(35),
        keep_messages=2,
    )
    middleware.wrap_model_call(
        _request(_messages(8), tools=["tool schema " * 30]),
        lambda request: seen.append(request) or _response(),
    )
    assert seen[0].messages[0].content.startswith("Here is a summary")


def test_compaction_projects_history_and_persists_event_without_mutating_raw() -> None:
    raw = _messages()
    seen = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: "structured summary",
        thresholds=_thresholds(),
        keep_messages=4,
    )
    result = middleware.wrap_model_call(
        _request(raw), lambda request: seen.append(request) or _response()
    )
    update = _command_update(result)
    assert len(raw) == 20
    assert len(seen[0].messages) == 5
    assert update["compaction"]["event"]["cutoff_index"] == 16
    assert "messages" not in update

    resumed_state = {"messages": [*raw, HumanMessage(content="new turn")], **update}
    projected = middleware._project(_request(resumed_state["messages"], resumed_state))
    assert len(projected.messages) == 1 + 4 + 1
    assert projected.messages[0].additional_kwargs["lc_source"] == "summarization"


@pytest.mark.parametrize("summary", ["", "<error> failed", "I cannot summarize this"])
def test_invalid_summary_does_not_publish_event(summary: str) -> None:
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: summary,
        thresholds=_thresholds(),
    )
    result = middleware.wrap_model_call(_request(_messages()), lambda request: _response())
    update = _command_update(result)
    assert update["compaction"]["consecutive_failures"] == 1
    assert "event" not in update["compaction"]


def test_summary_ptl_retry_drops_complete_tool_round() -> None:
    calls = []
    transcript = [
        HumanMessage(content="first"),
        AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "call-1"}]),
        ToolMessage(content="large", tool_call_id="call-1"),
        *_messages(12),
    ]

    def summarize(messages):
        calls.append(list(messages))
        if len(calls) == 1:
            raise ContextOverflowError("summary too long")
        return "summary after retry"

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=summarize,
        thresholds=_thresholds(),
        keep_messages=2,
    )
    middleware.wrap_model_call(_request(transcript), lambda request: _response())
    assert len(calls) == 2
    remaining_ids = {
        message.tool_call_id for message in calls[1] if isinstance(message, ToolMessage)
    }
    ai_ids = {
        call["id"]
        for message in calls[1]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    assert remaining_ids <= ai_ids


def test_reactive_overflow_retries_once_and_persists_recovery() -> None:
    attempts = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 1,
        summarize=lambda messages: "reactive summary",
        thresholds=_thresholds(),
        keep_messages=3,
    )

    def handler(request):
        attempts.append(len(request.messages))
        if len(attempts) == 1:
            raise ContextOverflowError("provider overflow")
        return _response()

    update = _command_update(middleware.wrap_model_call(_request(_messages()), handler))
    assert attempts == [20, 4]
    assert update["compaction"]["last_mode"] == "reactive"


def test_breaker_is_checkpointed_and_manual_compaction_still_works() -> None:
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: "",
        thresholds=_thresholds(),
        max_consecutive_failures=2,
    )
    state = {"messages": _messages()}
    for expected in (1, 2):
        update = _command_update(
            middleware.wrap_model_call(
                _request(state["messages"], state), lambda request: _response()
            )
        )
        state.update(update)
        assert state["compaction"]["consecutive_failures"] == expected

    result = middleware.wrap_model_call(
        _request(state["messages"], state), lambda request: _response()
    )
    assert not isinstance(result, ExtendedModelResponse)

    middleware._summarize = lambda messages: "manual summary"
    manual = middleware.compact(state, thread_id="thread-1", instructions="keep decisions")
    assert manual["compaction"]["last_mode"] == "manual"


def test_archive_failure_prevents_compaction_publication() -> None:
    class BrokenBackend:
        def write(self, path, content):
            raise OSError("disk full")

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: "summary",
        thresholds=_thresholds(),
        backend=BrokenBackend(),  # type: ignore[arg-type]
    )
    update = _command_update(
        middleware.wrap_model_call(_request(_messages()), lambda request: _response())
    )
    assert "event" not in update["compaction"]


@pytest.mark.asyncio
async def test_async_summary_path_uses_async_callable() -> None:
    async def summarize(messages):
        return "async summary"

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: "sync must not run",
        async_summarize=summarize,
        thresholds=_thresholds(),
        keep_messages=4,
    )

    async def handler(request):
        return _response()

    result = await middleware.awrap_model_call(_request(_messages()), handler)
    assert _command_update(result)["compaction"]["event"]
