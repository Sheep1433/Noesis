"""Single owner for model attempts, terminal reasons and empty tool turns."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.callbacks.manager import adispatch_custom_event, dispatch_custom_event

from noesis.middlewares.kernel.runtime_common import (
    has_tool_side_effect,
    last_ai_message,
    message_text,
    reason_from_finish,
    response_finish_reason,
    response_messages,
    set_outcome,
)
from noesis.runtime.model_attempt import current_model_attempt_tracker
from noesis.runtime.outcome import RuntimePhase, RuntimeStatus, StopReason, outcome

_logger = logging.getLogger(__name__)


def is_transient_model_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
        return True
    name = exc.__class__.__name__.lower()
    if any(token in name for token in ("timeout", "connection", "protocolerror", "ratelimit", "servererror")):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status in {408, 429} or status >= 500)


_EMPTY_AFTER_TOOLS_PROMPT = (
    "工具已经返回结果，但本轮没有生成正文或下一步工具调用。"
    "请基于现有工具结果直接给出最终答复；不要再次调用工具。"
)
_EMPTY_AFTER_TOOLS_FALLBACK = "工具已执行，但模型未生成后续答复。请根据工具结果继续处理。"


class ModelExecutionMiddleware(AgentMiddleware):
    """Retry and terminal interpretation at the innermost model boundary."""

    def __init__(self, *, max_retries: int = 0, base_delay_seconds: float = 0.25) -> None:
        self.max_retries = max(0, int(max_retries))
        self.base_delay_seconds = max(0.0, float(base_delay_seconds))

    async def _emit_retry_event(self, data: dict[str, Any]) -> None:
        try:
            await adispatch_custom_event("noesis_model_retry", data)
        except Exception:
            _logger.debug("failed to dispatch model retry event", exc_info=True)

    @staticmethod
    def _emit_retry_event_sync(data: dict[str, Any]) -> None:
        try:
            dispatch_custom_event("noesis_model_retry", data)
        except Exception:
            _logger.debug("failed to dispatch model retry event", exc_info=True)

    @staticmethod
    def _is_empty_after_tools(request: ModelRequest, response: Any) -> bool:
        if not any(getattr(message, "type", None) == "tool" for message in request.messages):
            return False
        ai = last_ai_message(response)
        if ai is None or getattr(ai, "tool_calls", None):
            return False
        return not message_text(ai).strip()

    @staticmethod
    def _annotate(response: Any, *, visible: bool, side_effect: bool) -> Any:
        finish = response_finish_reason(response)
        reason = reason_from_finish(finish)
        if reason is None:
            reason = StopReason.COMPLETED
        status = RuntimeStatus.STOP if reason != StopReason.COMPLETED else RuntimeStatus.CONTINUE
        set_outcome(
            outcome(
                RuntimePhase.MODEL,
                status,
                reason,
                visible_output_started=visible,
                side_effect_started=side_effect,
                detail={"provider_finish_reason": finish} if finish else {},
            )
        )
        return response

    def _attempt_state(self, response: Any) -> tuple[bool, bool]:
        messages = response_messages(response)
        visible = any(message_text(message).strip() for message in messages if getattr(message, "type", None) == "ai")
        side_effect = any(getattr(message, "tool_calls", None) for message in messages) or has_tool_side_effect(messages)
        tracker = current_model_attempt_tracker()
        if tracker is not None:
            visible = visible or tracker.visible_output_started
            side_effect = side_effect or tracker.side_effect_boundary_crossed
        return visible, side_effect

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        for index in range(self.max_retries + 1):
            try:
                response = handler(request)
                return self._finish(request, response, handler)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                tracker = current_model_attempt_tracker()
                visible = bool(tracker and tracker.visible_output_started)
                if index >= self.max_retries or not is_transient_model_error(exc) or not (tracker is None or tracker.can_retry):
                    reason = StopReason.PARTIAL_OUTPUT if visible else StopReason.RETRYABLE_ERROR
                    set_outcome(outcome(RuntimePhase.MODEL, RuntimeStatus.STOP if visible else RuntimeStatus.ERROR, reason, visible_output_started=visible, retryable=not visible and is_transient_model_error(exc), detail={"exception_type": type(exc).__name__}))
                    raise
                if tracker is not None:
                    tracker.attempt_id += 1
                attempt_id = tracker.attempt_id if tracker is not None else index + 2
                self._emit_retry_event_sync({
                    "status": "retrying",
                    "will_retry": True,
                    "attempt_id": attempt_id,
                    "attempt": index + 1,
                    "max_attempts": self.max_retries,
                    "message": "模型暂时不可用，正在重试",
                })
                if self.base_delay_seconds:
                    time.sleep(self.base_delay_seconds * (2**index))
                self._emit_retry_event_sync({
                    "status": "running",
                    "attempt_id": attempt_id,
                    "attempt": index + 1,
                    "max_attempts": self.max_retries,
                })

    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelResponse:
        for index in range(self.max_retries + 1):
            try:
                response = await handler(request)
                return await self._finish_async(request, response, handler)
            except BaseException as exc:
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                tracker = current_model_attempt_tracker()
                can_retry = tracker is None or tracker.can_retry
                if index >= self.max_retries or not is_transient_model_error(exc) or not can_retry:
                    visible = bool(tracker and tracker.visible_output_started)
                    reason = StopReason.PARTIAL_OUTPUT if visible else StopReason.RETRYABLE_ERROR
                    set_outcome(outcome(RuntimePhase.MODEL, RuntimeStatus.STOP if visible else RuntimeStatus.ERROR, reason, visible_output_started=visible, retryable=not visible and is_transient_model_error(exc), detail={"exception_type": type(exc).__name__}))
                    raise
                if tracker is not None:
                    tracker.attempt_id += 1
                attempt_id = tracker.attempt_id if tracker is not None else index + 2
                await self._emit_retry_event({
                    "status": "retrying",
                    "will_retry": True,
                    "attempt_id": attempt_id,
                    "attempt": index + 1,
                    "max_attempts": self.max_retries,
                    "message": "模型暂时不可用，正在重试",
                })
                if self.base_delay_seconds:
                    await asyncio.sleep(self.base_delay_seconds * (2**index))
                await self._emit_retry_event({
                    "status": "running",
                    "attempt_id": attempt_id,
                    "attempt": index + 1,
                    "max_attempts": self.max_retries,
                })
    def _finish(self, request: ModelRequest, response: ModelResponse, handler: Callable) -> ModelResponse:
        if self._is_empty_after_tools(request, response):
            retry_request = request.override(messages=[*request.messages, HumanMessage(content=_EMPTY_AFTER_TOOLS_PROMPT, name="runtime_instruction")])
            second = handler(retry_request)
            if self._is_empty_after_tools(retry_request, second):
                fallback = ModelResponse(result=[AIMessage(content=_EMPTY_AFTER_TOOLS_FALLBACK, response_metadata={"finish_reason": StopReason.EMPTY_AFTER_TOOLS.value})])
                set_outcome(outcome(RuntimePhase.MODEL, RuntimeStatus.STOP, StopReason.EMPTY_AFTER_TOOLS, visible_output_started=True, detail={"transient_prompt": True}))
                return fallback
            visible, side_effect = self._attempt_state(second)
            return self._annotate(second, visible=visible, side_effect=side_effect)
        visible, side_effect = self._attempt_state(response)
        return self._annotate(response, visible=visible, side_effect=side_effect)

    async def _finish_async(self, request: ModelRequest, response: ModelResponse, handler: Callable) -> ModelResponse:
        if self._is_empty_after_tools(request, response):
            retry_request = request.override(messages=[*request.messages, HumanMessage(content=_EMPTY_AFTER_TOOLS_PROMPT, name="runtime_instruction")])
            second = await handler(retry_request)
            if self._is_empty_after_tools(retry_request, second):
                fallback = ModelResponse(result=[AIMessage(content=_EMPTY_AFTER_TOOLS_FALLBACK, response_metadata={"finish_reason": StopReason.EMPTY_AFTER_TOOLS.value})])
                set_outcome(outcome(RuntimePhase.MODEL, RuntimeStatus.STOP, StopReason.EMPTY_AFTER_TOOLS, visible_output_started=True, detail={"transient_prompt": True}))
                return fallback
            visible, side_effect = self._attempt_state(second)
            return self._annotate(second, visible=visible, side_effect=side_effect)
        visible, side_effect = self._attempt_state(response)
        return self._annotate(response, visible=visible, side_effect=side_effect)
