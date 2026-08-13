"""Unit contracts for ``ToolFailureMiddleware`` (typed error translation)."""

from __future__ import annotations

import asyncio

import pytest
from langchain.agents.middleware.types import AgentState
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

from noesis.errors.tool_failure import ToolFailureCategory, ToolInfrastructureError
from noesis.middleware.tool_failure_middleware import ToolFailureMiddleware


def _call_request(name: str = "search", call_id: str = "call-1", args: dict | None = None) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": call_id},
        tool=None,
        state={"messages": []},
        runtime=None,
    )


def _handler_returning(msg: ToolMessage):
    def handler(_request):  # noqa: ANN001
        return msg

    return handler


def _handler_raising(exc: BaseException):
    def handler(_request):  # noqa: ANN001
        raise exc

    return handler


def test_generic_exception_becomes_typed_error_tool_message() -> None:
    mw = ToolFailureMiddleware()
    result = mw.wrap_tool_call(_call_request(), _handler_raising(RuntimeError("boom")))
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    assert result.name == "search"


def test_typed_failure_error_preserves_category() -> None:
    mw = ToolFailureMiddleware()
    exc = ToolInfrastructureError("disk full")
    result = mw.wrap_tool_call(_call_request(), _handler_raising(exc))
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    # the project's build_error_tool_message stamps errorCategory into additional_kwargs
    category = result.additional_kwargs.get("errorCategory") or result.additional_kwargs.get("error_category")
    assert category == ToolFailureCategory.INFRASTRUCTURE.value


def test_graph_interrupt_propagates_not_swallowed() -> None:
    mw = ToolFailureMiddleware()
    def handler(_request):  # noqa: ANN001
        raise GraphInterrupt()
    with pytest.raises(GraphInterrupt):
        mw.wrap_tool_call(_call_request(), handler)


def test_cancellation_propagates_not_swallowed() -> None:
    mw = ToolFailureMiddleware()
    def handler(_request):  # noqa: ANN001
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        mw.wrap_tool_call(_call_request(), handler)


def test_successful_result_passes_through_unchanged() -> None:
    mw = ToolFailureMiddleware()
    ok = ToolMessage(content="42", tool_call_id="call-1", name="search")
    result = mw.wrap_tool_call(_call_request(), _handler_returning(ok))
    assert result is ok


@pytest.mark.anyio
async def test_async_path_translates_exception() -> None:
    mw = ToolFailureMiddleware()

    async def handler(_request):  # noqa: ANN001
        raise RuntimeError("async boom")

    result = await mw.awrap_tool_call(_call_request(call_id="c-async"), handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "c-async"


@pytest.mark.anyio
async def test_async_graph_interrupt_propagates() -> None:
    mw = ToolFailureMiddleware()

    async def handler(_request):  # noqa: ANN001
        raise GraphInterrupt()

    with pytest.raises(GraphInterrupt):
        await mw.awrap_tool_call(_call_request(), handler)
