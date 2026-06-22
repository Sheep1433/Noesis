"""ToolErrorHandlingMiddleware：工具异常转 ToolMessage。"""
from __future__ import annotations

import httpx
import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from unittest.mock import MagicMock

from agent.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from domain.chat.streaming.tool_errors import (
    ToolFailureCategory,
    ToolInfrastructureError,
)


def _request() -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "bash", "args": {}, "id": "call_abc", "type": "tool_call"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )


def test_wrap_tool_call_returns_error_tool_message_from_httpx_cause() -> None:
    mw = ToolErrorHandlingMiddleware()

    def _boom(_req: ToolCallRequest) -> ToolMessage:
        try:
            raise httpx.ConnectError("connection refused")
        except httpx.ConnectError as cause:
            raise RuntimeError("页面抓取失败") from cause

    result = mw.wrap_tool_call(_request(), _boom)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_abc"
    assert "[tool_error category=network_unreachable" in result.content


def test_wrap_tool_call_unknown_for_bare_runtime_error_text() -> None:
    mw = ToolErrorHandlingMiddleware()

    def _boom(_req: ToolCallRequest) -> ToolMessage:
        raise RuntimeError("connection refused")

    result = mw.wrap_tool_call(_request(), _boom)
    assert isinstance(result, ToolMessage)
    assert "[tool_error category=unknown" in result.content


def test_wrap_tool_call_preserves_graph_bubble_up() -> None:
    mw = ToolErrorHandlingMiddleware()

    def _interrupt(_req: ToolCallRequest) -> ToolMessage:
        raise GraphBubbleUp(Command(goto="__end__"))

    with pytest.raises(GraphBubbleUp):
        mw.wrap_tool_call(_request(), _interrupt)


def test_wrap_tool_call_reclassifies_handler_error_status() -> None:
    mw = ToolErrorHandlingMiddleware()

    def _error_msg(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="ValidationError: missing field ip",
            tool_call_id="call_abc",
            name="bash",
            status="error",
        )

    result = mw.wrap_tool_call(_request(), _error_msg)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "[tool_error category=unknown" in result.content


def test_wrap_tool_call_passthrough_prefixed_error_content() -> None:
    mw = ToolErrorHandlingMiddleware()
    original = (
        "[tool_error category=subagent_failure retryable=false]\n"
        "Tool 'task' failed: child tool broke"
    )

    def _prefixed(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=original,
            tool_call_id="call_abc",
            name="task",
            status="error",
        )

    result = mw.wrap_tool_call(_request(), _prefixed)
    assert result.content == original


@pytest.mark.asyncio
async def test_awrap_tool_call_infrastructure_error() -> None:
    mw = ToolErrorHandlingMiddleware()

    async def _boom(_req: ToolCallRequest) -> ToolMessage:
        raise ToolInfrastructureError("[INTERNAL_ERROR] Docker image ubuntu:latest not found")

    result = await mw.awrap_tool_call(_request(), _boom)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "[tool_error category=infrastructure" in result.content
    assert ToolFailureCategory.INFRASTRUCTURE.value in result.content


@pytest.mark.asyncio
async def test_awrap_tool_call_internal_error_text_without_typed_exc_is_unknown() -> None:
    mw = ToolErrorHandlingMiddleware()

    async def _boom(_req: ToolCallRequest) -> ToolMessage:
        raise RuntimeError("[INTERNAL_ERROR] Docker image ubuntu:latest not found")

    result = await mw.awrap_tool_call(_request(), _boom)
    assert isinstance(result, ToolMessage)
    assert "[tool_error category=unknown" in result.content
