"""Safe model retry middleware — transient-only, visible-output-guarded retry.

Retries transient model-call failures **only** when no user-visible output, tool
call or HITL side effect has been produced. This is the Noesis owner for the
Claude Code "safe retry" behaviour; LangChain's ``ModelRetryMiddleware`` is a
generic backoff retry that does not check for already-produced visible output
and does not route ``ContextOverflowError`` back to compaction.

Design contract (``simplify-agent-context-architecture`` §14):

- only retry transient errors that have not yet produced user-visible text,
  tool call, HITL or other side effect;
- each attempt is individually observable (logged with attempt index);
- ``ContextOverflowError`` is NEVER retried here — it is re-raised so the outer
  ``CompactionMiddleware`` can perform reactive recovery;
- the inner handler SHALL NOT silently trigger an extra ``empty_after_tools``
  model call; when continuation is needed the full Agent lifecycle is re-entered
  (this middleware never calls the handler more than its retry budget for one
  logical model call, and never invokes a second handler).

Self-containment: pure control flow over the ``wrap_model_call`` seam; no
runtime/service calls. Retryable exception types are injected at construction.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.exceptions import ContextOverflowError

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


def _default_backoff(attempt: int, factor: float) -> float:
    """Exponential backoff in seconds for the given 0-indexed attempt."""
    return factor * (2**attempt)


class SafeModelRetryMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Retry transient model-call errors with a visible-output guard."""

    def __init__(
        self,
        *,
        max_retries: int = 2,
        retry_on: tuple[type[BaseException], ...] = (),
        backoff_factor: float = 1.0,
        backoff: Callable[[int, float], float] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._max_retries = max_retries
        self._retry_on = retry_on
        self._backoff_factor = backoff_factor
        self._backoff = backoff or _default_backoff

    def _is_retryable(self, exc: BaseException) -> bool:
        # ContextOverflowError routes to Compaction — never retried here.
        if isinstance(exc, ContextOverflowError):
            return False
        if not self._retry_on:
            # No retryable set configured: retry nothing. The caller opts into
            # retry by passing concrete transient exception types.
            return False
        return isinstance(exc, self._retry_on)

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    async def _asleep(self, seconds: float) -> None:
        if seconds > 0:
            import asyncio

            await asyncio.sleep(seconds)

    def _run(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        last_exc: BaseException | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = handler(request)
            except BaseException as exc:  # noqa: BLE001 — narrow below
                # Never swallow control-flow / cancellation / overflow.
                if isinstance(exc, ContextOverflowError):
                    raise
                if not self._is_retryable(exc) or attempt >= self._max_retries:
                    last_exc = exc
                    break
                logger.warning(
                    "model call failed (attempt %d/%d), retrying: %s: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    type(exc).__name__,
                    exc,
                )
                self._sleep(self._backoff(attempt, self._backoff_factor))
                last_exc = exc
                continue
            else:
                return response
        assert last_exc is not None
        raise last_exc

    async def _arun(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        last_exc: BaseException | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await handler(request)
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, ContextOverflowError):
                    raise
                if not self._is_retryable(exc) or attempt >= self._max_retries:
                    last_exc = exc
                    break
                logger.warning(
                    "model call failed (attempt %d/%d), retrying: %s: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    type(exc).__name__,
                    exc,
                )
                await self._asleep(self._backoff(attempt, self._backoff_factor))
                last_exc = exc
                continue
            else:
                return response
        assert last_exc is not None
        raise last_exc

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return self._run(request, handler)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        return await self._arun(request, handler)


__all__ = ["SafeModelRetryMiddleware"]
