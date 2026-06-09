"""ToolErrorHandlingMiddleware：工具异常转 ToolMessage。"""
from __future__ import annotations

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from unittest.mock import MagicMock

from agent.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware


def _request() -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "bash", "args": {}, "id": "call_abc", "type": "tool_call"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )


def test_wrap_tool_call_returns_error_tool_message() -> None:
    mw = ToolErrorHandlingMiddleware()

    def _boom(_req: ToolCallRequest) -> ToolMessage:
        raise RuntimeError("connection refused")

    result = mw.wrap_tool_call(_request(), _boom)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_abc"
    assert "connection refused" in result.content


def test_wrap_tool_call_preserves_graph_bubble_up() -> None:
    mw = ToolErrorHandlingMiddleware()

    def _interrupt(_req: ToolCallRequest) -> ToolMessage:
        raise GraphBubbleUp(Command(goto="__end__"))

    with pytest.raises(GraphBubbleUp):
        mw.wrap_tool_call(_request(), _interrupt)


@pytest.mark.asyncio
async def test_awrap_tool_call_returns_error_tool_message() -> None:
    mw = ToolErrorHandlingMiddleware()

    async def _boom(_req: ToolCallRequest) -> ToolMessage:
        raise RuntimeError("[INTERNAL_ERROR] Docker image ubuntu:latest not found")

    result = await mw.awrap_tool_call(_request(), _boom)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "工具执行环境不可用" in result.content
