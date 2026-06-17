"""将工具异常转为 error ToolMessage，避免整轮 Agent 崩溃。"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from common.logging import logger
from domain.chat.streaming.tool_failure import (
    TOOL_ERROR_PREFIX,
    build_error_tool_message,
    classify_tool_failure,
)

_MISSING_TOOL_CALL_ID = "missing_tool_call_id"


class ToolErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Convert tool exceptions into error ToolMessages so the run can continue."""

    def _tool_name(self, request: ToolCallRequest) -> str:
        return str(request.tool_call.get("name") or "unknown_tool")

    def _tool_call_id(self, request: ToolCallRequest) -> str:
        return str(request.tool_call.get("id") or _MISSING_TOOL_CALL_ID)

    def _build_error_message(self, request: ToolCallRequest, exc: Exception) -> ToolMessage:
        tool_name = self._tool_name(request)
        failure = classify_tool_failure(exc, tool_name=tool_name)
        logger.exception(
            "Tool execution failed: name=%s id=%s tool_failure_category=%s",
            request.tool_call.get("name"),
            request.tool_call.get("id"),
            failure.category.value,
        )
        return build_error_tool_message(request, failure)

    def _normalize_error_tool_message(
        self,
        request: ToolCallRequest,
        result: ToolMessage,
    ) -> ToolMessage:
        content = str(result.content or "").lstrip()
        if content.startswith(TOOL_ERROR_PREFIX):
            return result
        tool_name = self._tool_name(request)
        failure = classify_tool_failure(None, raw=content, tool_name=tool_name)
        logger.warning(
            "Tool returned status=error: name=%s id=%s tool_failure_category=%s",
            request.tool_call.get("name"),
            request.tool_call.get("id"),
            failure.category.value,
        )
        return build_error_tool_message(request, failure)

    def _finalize_result(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command,
    ) -> ToolMessage | Command:
        if isinstance(result, ToolMessage) and result.status == "error":
            return self._normalize_error_tool_message(request, result)
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            return self._finalize_result(request, handler(request))
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return self._build_error_message(request, exc)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return self._finalize_result(request, await handler(request))
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return self._build_error_message(request, exc)
