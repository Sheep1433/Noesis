"""Attempt-aware retry for transient model failures before any visible output."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.callbacks.manager import adispatch_custom_event

from noesis.runtime.model_attempt import current_model_attempt_tracker


def is_transient_model_error(exc: BaseException) -> bool:
    """Classify transport, timeout, rate-limit and server failures without provider coupling."""
    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
        return True
    name = exc.__class__.__name__.lower()
    if any(token in name for token in ("timeout", "connection", "ratelimit", "servererror")):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status in {408, 409, 429} or status >= 500)


class ModelRetryMiddleware(AgentMiddleware):
    """Retry only while the run has emitted no token and crossed no tool/HITL boundary."""

    def __init__(self, *, max_retries: int, base_delay_seconds: float = 0.25) -> None:
        self.max_retries = max(0, int(max_retries))
        self.base_delay_seconds = max(0.0, float(base_delay_seconds))

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        # Sync callers retain provider behavior; the durable run path is async.
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        tracker = current_model_attempt_tracker()
        for retry_index in range(self.max_retries + 1):
            try:
                return await handler(request)
            except BaseException as exc:
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                may_retry = (
                    retry_index < self.max_retries
                    and tracker is not None
                    and tracker.can_retry
                    and is_transient_model_error(exc)
                )
                if not may_retry:
                    raise
                tracker.attempt_id += 1
                attempt_id = tracker.attempt_id
                await adispatch_custom_event(
                    "noesis_model_retry",
                    {
                        "status": "retrying",
                        "will_retry": True,
                        "attempt_id": attempt_id,
                        "attempt": retry_index + 1,
                        "max_attempts": self.max_retries,
                        "message": "模型暂时不可用，正在重试",
                    },
                )
                delay = self.base_delay_seconds * (2**retry_index)
                if delay:
                    await asyncio.sleep(delay)
                await adispatch_custom_event(
                    "noesis_model_retry",
                    {
                        "status": "running",
                        "attempt_id": attempt_id,
                        "attempt": retry_index + 1,
                        "max_attempts": self.max_retries,
                    },
                )
        raise RuntimeError("unreachable model retry state")
