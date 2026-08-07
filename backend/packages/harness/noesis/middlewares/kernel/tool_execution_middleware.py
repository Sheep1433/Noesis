"""Single owner for typed tool failures, envelopes and fallback bounding."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from time import monotonic
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from noesis.config.env import ModelConfig
from noesis.errors.tool_failure import (
    TOOL_ERROR_PREFIX,
    build_error_tool_message,
    classify_tool_failure,
    format_tool_error_detail,
)
from noesis.middlewares.kernel.runtime_common import content_size, tool_message_fields
from noesis.runtime.logging import logger
from noesis.runtime.outcome import RuntimePhase, RuntimeStatus, StopReason, ToolResultEnvelope, outcome, set_runtime_outcome, set_tool_result_envelope
from noesis.runtime.governor import bind_run_governor, current_run_governor


def _content_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, dict) and item.get("type") == "text" for item in value):
        return "".join(str(item.get("text", "")) for item in value)
    return None


def _is_deepagents_bounded(message: ToolMessage) -> bool:
    metadata = dict(getattr(message, "additional_kwargs", {}) or {})
    return any(
        metadata.get(key)
        for key in ("deepagents_offloaded", "large_tool_result", "tool_result_offloaded")
    )


class ToolExecutionMiddleware(AgentMiddleware[AgentState]):
    def __init__(self, *, max_output_chars: int | None = None, head_chars: int = 8_000, tail_chars: int = 8_000) -> None:
        super().__init__()
        configured = int(getattr(ModelConfig, "tool_output_max_chars", 24_000))
        self.max_output_chars = max_output_chars if max_output_chars is not None else max(1, configured)
        self.head_chars = max(0, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))

    @staticmethod
    def _tool_name(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("name") or "unknown_tool")

    @staticmethod
    def _tool_call_id(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("id") or "missing_tool_call_id")

    def _error_message(self, request: ToolCallRequest, exc: Exception) -> ToolMessage:
        failure = classify_tool_failure(exc, tool_name=self._tool_name(request))
        logger.error("tool execution failed name=%s id=%s category=%s detail=%s", self._tool_name(request), self._tool_call_id(request), failure.category.value, format_tool_error_detail(exc))
        return build_error_tool_message(request, failure)

    def _normalize_error(self, request: ToolCallRequest, result: ToolMessage) -> ToolMessage:
        content = str(result.content or "").lstrip()
        if content.startswith(TOOL_ERROR_PREFIX):
            return result
        failure = classify_tool_failure(None, raw=content, tool_name=self._tool_name(request))
        return build_error_tool_message(request, failure)

    def _bound(self, result: ToolMessage) -> tuple[ToolMessage, str, int | None, int | None]:
        original = content_size(result.content)
        deepagents_bounded = _is_deepagents_bounded(result)
        if deepagents_bounded or self.max_output_chars is None or original <= self.max_output_chars:
            return result, "deepagents" if deepagents_bounded else "none", None, None
        text = _content_text(result.content)
        if text is None:
            return result, "none", None, None
        limit = self.max_output_chars
        head = min(self.head_chars, limit)
        tail = min(self.tail_chars, max(0, limit - head))
        omitted = max(0, len(text) - head - tail)
        marker = f"\n… [tool output truncated; omitted {omitted} chars] …\n"
        clipped = text[:head] + marker + (text[-tail:] if tail else "")
        return result.model_copy(update={"content": clipped}), "noesis_fallback", len(text), omitted

    def _envelope(self, request: ToolCallRequest, result: ToolMessage | Command, started: float) -> tuple[ToolResultEnvelope | None, ToolMessage | Command]:
        if not isinstance(result, ToolMessage):
            return None, result
        bounded, bounded_by, original_size, omitted_size = self._bound(result)
        if bounded is not result:
            result = bounded
        status, parsed_outcome = tool_message_fields(result)
        metadata = dict(getattr(result, "additional_kwargs", {}) or {})
        category = metadata.get("errorCategory") or metadata.get("error_category")
        if status == "error" and not category:
            category = classify_tool_failure(None, raw=str(result.content or ""), tool_name=self._tool_name(request)).category.value
        envelope = ToolResultEnvelope(
            tool_call_id=self._tool_call_id(request),
            tool_name=self._tool_name(request),
            status=status or "success",
            content=result.content,
            category=str(category) if category else None,
            outcome=parsed_outcome,
            bounded_by=bounded_by,
            original_size=original_size,
            omitted_size=omitted_size,
            timing_ms=(monotonic() - started) * 1000,
        )
        set_tool_result_envelope(envelope)
        set_runtime_outcome(outcome(RuntimePhase.TOOL, RuntimeStatus.CONTINUE, StopReason.COMPLETED, side_effect_started=True, detail=envelope.public_dict()))
        return envelope, result

    def _process_result(self, request: ToolCallRequest, result: ToolMessage | Command, started: float) -> ToolMessage | Command:
        if isinstance(result, ToolMessage):
            _, processed = self._envelope(request, result, started)
            return processed
        if isinstance(result, Command) and result.update:
            messages = result.update.get("messages", [])
            processed_messages = []
            for message in messages:
                if isinstance(message, ToolMessage):
                    if message.status == "error":
                        message = self._normalize_error(request, message)
                    _, processed = self._envelope(request, message, started)
                    processed_messages.append(processed)
                else:
                    processed_messages.append(message)
            return Command(
                goto=result.goto,
                graph=result.graph,
                update={**result.update, "messages": processed_messages},
            )
        return result

    def _reserve(self, request: ToolCallRequest):
        governor = current_run_governor()
        subagent_reserved = False
        if governor is not None and self._tool_name(request) == "task":
            stopped = governor.reserve_subagent()
            if stopped is not None:
                set_runtime_outcome(stopped)
                return governor, False, ToolMessage(
                    content=f"子 Agent 调用已停止（{stopped.wire_reason}）。",
                    tool_call_id=self._tool_call_id(request),
                    name=self._tool_name(request),
                    status="error",
                )
            subagent_reserved = True
        if governor is not None:
            stopped = governor.reserve_tool(self._tool_name(request))
            if stopped is not None:
                if subagent_reserved:
                    governor.release_subagent()
                set_runtime_outcome(stopped)
                return governor, False, ToolMessage(
                    content=f"工具调用已停止（{stopped.wire_reason}）。",
                    tool_call_id=self._tool_call_id(request),
                    name=self._tool_name(request),
                    status="error",
                )
        return governor, subagent_reserved, None

    def _finish_result(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command,
        started: float,
        governor=None,
    ) -> ToolMessage | Command:
        if isinstance(result, ToolMessage) and result.status == "error":
            result = self._normalize_error(request, result)
        processed = self._process_result(request, result, started)
        if governor is None:
            return processed
        snapshot = governor.state.snapshot()
        if isinstance(processed, ToolMessage):
            return Command(
                update={"messages": [processed], "noesis_governor": snapshot}
            )
        if isinstance(processed, Command):
            return Command(
                goto=processed.goto,
                graph=processed.graph,
                update={**(processed.update or {}), "noesis_governor": snapshot},
            )
        return processed

    def _run(self, request: ToolCallRequest, handler: Callable):
        governor, subagent_reserved, stopped = self._reserve(request)
        if stopped is not None:
            return stopped
        started = monotonic()
        try:
            child_scope = (
                bind_run_governor(governor.child(f"{governor.state.run_id}:{self._tool_call_id(request)}"))
                if subagent_reserved and governor is not None
                else nullcontext()
            )
            with child_scope:
                try:
                    result = handler(request)
                except GraphBubbleUp:
                    raise
                except Exception as exc:
                    result = self._error_message(request, exc)
        finally:
            if subagent_reserved:
                governor.release_subagent()
        return self._finish_result(request, result, started, governor)

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]) -> ToolMessage | Command:
        return self._run(request, handler)

    async def awrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]) -> ToolMessage | Command:
        governor, subagent_reserved, stopped = self._reserve(request)
        if stopped is not None:
            return stopped
        started = monotonic()
        try:
            child_scope = (
                bind_run_governor(governor.child(f"{governor.state.run_id}:{self._tool_call_id(request)}"))
                if subagent_reserved and governor is not None
                else nullcontext()
            )
            with child_scope:
                try:
                    result = await handler(request)
                except GraphBubbleUp:
                    raise
                except Exception as exc:
                    result = self._error_message(request, exc)
        finally:
            if subagent_reserved:
                governor.release_subagent()
        return self._finish_result(request, result, started, governor)
