"""Safe model retry middleware — transient HTTP error retry at the model-call seam.

Retries transient model-call failures (429 / 408 / 5xx, timeout, connection)
with exponential backoff, mirroring the Claude Code 2.1.88 behaviour: the
Anthropic SDK retries at the HTTP layer (``maxRetries=2``, ``shouldRetry`` on
status code + ``x-should-retry`` header) before the streaming body begins. This
middleware is a fallback when the SDK's own retry is disabled or insufficient,
and for non-SDK providers.

This is the Noesis owner for model-call retry; LangChain's ``ModelRetryMiddleware``
is a generic backoff retry without ``ContextOverflowError`` routing.

Design contract (``simplify-agent-context-architecture`` §14):

- retry transient errors only (408/429/>=500, timeout, connection);
- each attempt reuses the same already-canonicalised/compacted/budget-checked
  request and is individually observable (logged with attempt index);
- ``ContextOverflowError`` is NEVER retried here — re-raised so the outer
  ``CompactionMiddleware`` performs reactive recovery;
- the inner handler SHALL NOT silently trigger an extra ``empty_after_tools``
  model call.

Self-containment: pure control flow over the ``wrap_model_call`` seam; no
runtime/service calls.
"""

from __future__ import annotations

import asyncio
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


# Exception class-name fragments that indicate a transient failure across
# providers (anthropic/openai/deepseek name them differently). Mirrors the
# old is_transient_model_error heuristic.
_TRANSIENT_NAME_TOKENS = (
    "timeout", "connection", "protocolerror", "ratelimit", "servererror",
)

# HTTP status codes that are transient and safe to retry.
_TRANSIENT_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})


def _status_code_of(exc: BaseException) -> int | None:
    """Extract an HTTP status code from a provider exception, if present."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    if response is not None:
        return getattr(response, "status_code", None)
    return None


def is_transient_model_error(exc: BaseException) -> bool:
    """True if the exception represents a transient, retryable model-call failure.

    Matches the Claude Code SDK ``shouldRetry`` contract:
    - 408 / 409 / 429 / >=500 → retryable;
    - timeout / connection errors → retryable;
    - provider exception class names containing transient tokens → retryable.
    """
    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
        return True
    name = type(exc).__name__.lower()
    if any(token in name for token in _TRANSIENT_NAME_TOKENS):
        return True
    status = _status_code_of(exc)
    if isinstance(status, int) and (status in _TRANSIENT_STATUS_CODES or status >= 500):
        return True
    return False


def _default_backoff(attempt: int, factor: float) -> float:
    """Exponential backoff: ``factor * 2^attempt``, capped at 8s (like Claude Code SDK)."""
    return min(factor * (2**attempt), 8.0)


class SafeModelRetryMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Retry transient model-call errors with exponential backoff."""

    def __init__(
        self,
        *,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
        backoff: Callable[[int, float], float] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._backoff = backoff or _default_backoff

    def _is_retryable(self, exc: BaseException) -> bool:
        # ContextOverflowError routes to Compaction — never retried here.
        if isinstance(exc, ContextOverflowError):
            return False
        return is_transient_model_error(exc)

    def _run(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        last_exc: BaseException | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return handler(request)
            except ContextOverflowError:
                raise
            except BaseException as exc:  # noqa: BLE001 — narrowed below
                if not self._is_retryable(exc) or attempt >= self._max_retries:
                    raise
                logger.warning(
                    "model call failed (attempt %d/%d), retrying: %s: %s",
                    attempt + 1,
                    self._max_retries,
                    type(exc).__name__,
                    exc,
                )
                time.sleep(self._backoff(attempt, self._backoff_factor))
                last_exc = exc
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
                return await handler(request)
            except ContextOverflowError:
                raise
            except BaseException as exc:  # noqa: BLE001
                if not self._is_retryable(exc) or attempt >= self._max_retries:
                    raise
                logger.warning(
                    "model call failed (attempt %d/%d), retrying: %s: %s",
                    attempt + 1,
                    self._max_retries,
                    type(exc).__name__,
                    exc,
                )
                await asyncio.sleep(self._backoff(attempt, self._backoff_factor))
                last_exc = exc
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


__all__ = ["SafeModelRetryMiddleware", "is_transient_model_error"]
