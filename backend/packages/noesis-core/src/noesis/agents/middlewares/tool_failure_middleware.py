"""Tool failure middleware — typed error ToolMessage translation.

Translates ordinary tool-call exceptions into a ``status="error"`` ToolMessage
paired with the original ``tool_call_id``, so the model can keep reasoning on a
well-formed tool-call/result history. This is the single Noesis owner for
exception→error-ToolMessage translation; upstream writes ``status="error"``
ad-hoc in several places with free-form text and no typed
``errorCategory``/``outcome`` envelope.

Design contract (``simplify-agent-context-architecture`` §14, and the
``agent-tool-failure-handling`` spec delta):

- only translates ordinary exceptions; LangGraph control exceptions
  (``GraphBubbleUp`` — covers ``GraphInterrupt`` / HITL), whole-turn
  cancellation (``asyncio.CancelledError``) and context overflow are
  re-raised so they keep their control semantics;
- the error ToolMessage carries the same ``tool_call_id`` as the call;
- typed ``ToolFailureError`` raised by tool adapters is honoured as-is;
- generic exception translation SHALL NOT also manage run budget, subagent
  scope, telemetry or artifact lifecycle (those are separate owners).

Self-containment: depends only on the project's pure classification module
``noesis.errors.tool_failure`` (no service/runtime calls) and the LangGraph
tool-call seam.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ResponseT,
)
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from noesis.errors.tool_failure import (
    build_error_tool_message,
    classify_tool_failure,
    format_tool_error_detail,
)
from noesis.runtime.logging import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable

# Exceptions that must propagate unchanged to preserve control flow.
# GraphBubbleUp is the LangGraph base for GraphInterrupt/HITL and other
# control-flow signals; CancelledError is whole-turn cancellation;
# KeyboardInterrupt is never swallowed.
_PROPAGATED_EXCEPTIONS = (GraphBubbleUp, asyncio.CancelledError, KeyboardInterrupt)


class ToolFailureMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Translate tool-call exceptions into typed error ToolMessages."""

    @staticmethod
    def _tool_name(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("name") or "unknown_tool")

    @staticmethod
    def _tool_call_id(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("id") or "missing_tool_call_id")

    def _error_message(self, request: ToolCallRequest, exc: BaseException) -> ToolMessage:
        tool_name = self._tool_name(request)
        failure = classify_tool_failure(exc, tool_name=tool_name)
        logger.error(
            "tool execution failed name={} id={} category={} detail={}",
            tool_name,
            self._tool_call_id(request),
            failure.category.value,
            format_tool_error_detail(exc),
        )
        return build_error_tool_message(request, failure)

    def _run(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            return handler(request)
        except _PROPAGATED_EXCEPTIONS:
            raise
        except Exception as exc:
            return self._error_message(request, exc)

    async def _arun(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except _PROPAGATED_EXCEPTIONS:
            raise
        except Exception as exc:
            return self._error_message(request, exc)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return self._run(request, handler)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        return await self._arun(request, handler)


__all__ = ["ToolFailureMiddleware"]
