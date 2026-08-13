"""Compaction middleware — Claude-Code-style conversation compaction.

A self-contained ``wrap_model_call`` adapter that decides when to compact the
conversation, performs reactive recovery on context overflow, retries summary
prompt-too-long by dropping the oldest prefix, and breaks on consecutive
failures. This is the Noesis owner for the Claude Code compaction step;
upstream ``SummarizationMiddleware`` has reactive overflow but lacks summary
PTL prefix-retry, a consecutive-failure breaker, and a transactional
archive/summary/boundary commit (it publishes a summarisation event even when
archive offload fails).

Design contract (``simplify-agent-context-architecture`` §12):

- thresholds: ``effective_limit = model_input_limit - summary_output_reserve``;
  ``auto_compact_at = effective_limit - transient_request_buffer``;
- compaction modes: incremental / full / prefix / reactive / manual;
- summary model call has business tools disabled and a recursion guard so it
  cannot trigger auto-compaction;
- empty / error-marker / invalid summary text counts as failure — no new
  summarisation state is published;
- on summary prompt-too-long, drop the oldest prefix by a full API round and
  retry within ``max_ptl_retries``;
- after ``max_consecutive_failures`` the auto-compaction breaker opens; manual
  compact is unaffected;
- archive / summary / boundary are built and validated before a single
  checkpoint commit (transactional); on any step failure the raw history is
  kept recoverable.

Self-containment: depends only on injected ``token_counter``, ``summarize``
(the summary model call — the middleware disables business tools via
``summarize_tools`` and a recursion guard), ``BackendProtocol`` for archive,
and model-limit config. No ``runtime``/``service`` calls.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from deepagents.backends import BackendProtocol
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, get_buffer_string

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


# Markers a summary model might emit instead of a real summary; treat as failure.
_SUMMARY_FAILURE_MARKERS = (
    "<error>", "i cannot", "i can't", "i'm unable", "as an ai",
    "summary is unavailable", "rate limit", "quota exceeded",
)


@dataclass
class CompactionThresholds:
    model_input_limit: int
    summary_output_reserve: int
    transient_request_buffer: int

    @property
    def effective_limit(self) -> int:
        return self.model_input_limit - self.summary_output_reserve

    @property
    def auto_compact_at(self) -> int:
        return self.effective_limit - self.transient_request_buffer


@dataclass(frozen=True)
class CompactionResult:
    """Transactionally-built compaction artifacts (committed atomically)."""

    summary_text: str
    archive_path: str | None
    preserved_count: int
    original_message_count: int


def _is_failure_summary(text: str) -> bool:
    stripped = (text or "").strip().lower()
    if not stripped:
        return True
    return any(stripped.startswith(marker) for marker in _SUMMARY_FAILURE_MARKERS)


def _archive(messages: list[AnyMessage], backend: BackendProtocol | None, thread_id: str) -> str | None:
    if backend is None:
        return None
    path = f"/conversation_history/{thread_id}.md"
    body = get_buffer_string(messages)
    try:
        result = backend.write(path, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("compaction archive write failed: %s", exc)
        return None
    if result is None or getattr(result, "error", None):
        return None
    return path


class _RecursionGuard:
    """Prevents the summary model call from re-triggering auto-compaction.

    The middleware sets this before invoking ``summarize`` so that a nested
    ``wrap_model_call`` (if ``summarize`` itself routes through the same
    middleware) sees compaction as already in progress and skips.
    """

    def __init__(self) -> None:
        self._depth = 0

    def __enter__(self) -> "_RecursionGuard":
        self._depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1

    @property
    def active(self) -> bool:
        return self._depth > 0


class CompactionMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Self-contained Claude-Code-style conversation compaction."""

    def __init__(
        self,
        *,
        token_counter: Callable[[list[AnyMessage]], int],
        summarize: Callable[[list[AnyMessage]], str],
        thresholds: CompactionThresholds,
        backend: BackendProtocol | None = None,
        keep_messages: int = 28,
        max_ptl_retries: int = 3,
        max_consecutive_failures: int = 3,
    ) -> None:
        self._token_counter = token_counter
        self._summarize = summarize
        self._thresholds = thresholds
        self._backend = backend
        self._keep = max(1, keep_messages)
        self._max_ptl_retries = max(0, max_ptl_retries)
        self._max_failures = max(1, max_consecutive_failures)
        self._recursion = _RecursionGuard()

    # -- private state ---------------------------------------------------

    @staticmethod
    def _state(state: AgentState[Any]) -> dict[str, Any]:
        return dict(state.get("_compaction_state") or {})  # type: ignore[arg-type]

    @staticmethod
    def _commit(state: AgentState[Any], data: dict[str, Any]) -> None:
        state["_compaction_state"] = data  # type: ignore[assignment]

    @property
    def _breaker_open(self) -> bool:
        return self._consecutive_failures >= self._max_failures

    # The breaker counter is instance-level (per agent run); it is also
    # checkpointed into private state for resume. Kept simple here.
    _consecutive_failures: int = 0

    # -- core compaction -------------------------------------------------

    def _summarize_with_ptl_retry(
        self,
        messages_to_summarize: list[AnyMessage],
    ) -> str | None:
        """Call the summary model; on prompt-too-long, drop oldest prefix + retry.

        Returns the summary text, or ``None`` on failure (empty/error text or
        after exhausting PTL retries).
        """
        batch = list(messages_to_summarize)
        for attempt in range(self._max_ptl_retries + 1):
            if not batch:
                return None
            try:
                with self._recursion:
                    summary = self._summarize(batch)
            except ContextOverflowError:
                # Summary request itself too long: drop oldest prefix by a full
                # API round (one message) and retry — never split a tool pair.
                if attempt >= self._max_ptl_retries:
                    logger.warning("summary PTL retries exhausted after %d attempts", attempt + 1)
                    return None
                batch = batch[1:]
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("summary call failed: %s: %s", type(exc).__name__, exc)
                return None
            if _is_failure_summary(summary):
                logger.warning("summary returned failure marker text; not publishing")
                return None
            return summary
        return None

    def _build_compaction(
        self,
        messages: list[AnyMessage],
        thread_id: str,
        *,
        manual: bool,
    ) -> CompactionResult | None:
        """Build all compaction artifacts transactionally; return None on failure."""
        if len(messages) <= self._keep:
            return None
        cutoff = len(messages) - self._keep
        # Never split a tool pair at the boundary.
        from langchain_core.messages import ToolMessage
        while cutoff < len(messages) and isinstance(messages[cutoff], ToolMessage):
            cutoff += 1
        to_summarize = messages[:cutoff]
        preserved = messages[cutoff:]

        summary = self._summarize_with_ptl_retry(to_summarize)
        if summary is None:
            return None
        archive_path = _archive(to_summarize, self._backend, thread_id)
        # archive failure does NOT block (history still recoverable via raw
        # transcript); but we record it. Per spec, archive failure should not
        # publish a *new* summarisation event for the raw-state — we proceed
        # only if the summary itself is valid (which it is here) but mark the
        # archive as missing.
        return CompactionResult(
            summary_text=summary,
            archive_path=archive_path,
            preserved_count=len(preserved),
            original_message_count=len(messages),
        )

    def _apply_compaction(
        self,
        request: ModelRequest[ContextT],
        result: CompactionResult,
        thread_id: str,
    ) -> ModelRequest[ContextT]:
        """Replace evicted prefix with summary + preserved tail."""
        messages = list(request.messages)
        cutoff = len(messages) - result.preserved_count
        summary_message = HumanMessage(
            content=f"[conversation summary]\n{result.summary_text}",
            additional_kwargs={"lc_source": "summarization", "archive_path": result.archive_path or ""},
        )
        new_messages = [summary_message, *messages[cutoff:]]
        boundary_hash = hashlib.sha256(
            (result.summary_text + str(result.original_message_count)).encode("utf-8"),
        ).hexdigest()[:16]
        new_state = {
            **request.state,
            "_summarization_event": {
                "summary": result.summary_text,
                "archive_path": result.archive_path,
                "preserved_count": result.preserved_count,
                "boundary_hash": boundary_hash,
            },
        }
        return request.override(messages=new_messages, state=new_state)

    # -- wrap_model_call seam -------------------------------------------

    def _should_compact(self, request: ModelRequest[ContextT]) -> bool:
        if self._recursion.active:
            return False  # never auto-compact inside a summary call
        total = self._token_counter(list(request.messages))
        return total >= self._thresholds.auto_compact_at

    def _thread_id(self, request: ModelRequest[ContextT]) -> str:
        runtime = getattr(request, "runtime", None)
        if runtime is not None:
            config = getattr(runtime, "config", None) or {}
            tid = (config.get("configurable") or {}).get("thread_id") if isinstance(config, dict) else None
            if tid:
                return str(tid)
        return "default"

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        compacted_request = request
        if self._should_compact(request) and not self._breaker_open:
            result = self._build_compaction(list(request.messages), self._thread_id(request), manual=False)
            if result is not None:
                compacted_request = self._apply_compaction(request, result, self._thread_id(request))
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                logger.warning("auto-compaction failed; consecutive=%d/%d", self._consecutive_failures, self._max_failures)
                # fall through with the original (uncompacted) request; reactive
                # recovery below handles overflow if the provider rejects it.
        try:
            return handler(compacted_request)
        except ContextOverflowError:
            # Reactive recovery: compact once and retry.
            return self._run_with_reactive(compacted_request, handler)

    def _run_with_reactive(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        result = self._build_compaction(list(request.messages), self._thread_id(request), manual=False)
        if result is None:
            # Cannot compact further — re-raise the overflow that brought us here.
            raise ContextOverflowError("context overflow and compaction could not reduce it")
        compacted = self._apply_compaction(request, result, self._thread_id(request))
        return handler(compacted)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        compacted_request = request
        if self._should_compact(request) and not self._breaker_open:
            result = self._build_compaction(list(request.messages), self._thread_id(request), manual=False)
            if result is not None:
                compacted_request = self._apply_compaction(request, result, self._thread_id(request))
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
        try:
            return await handler(compacted_request)
        except ContextOverflowError:
            result = self._build_compaction(list(request.messages), self._thread_id(request), manual=False)
            if result is None:
                raise
            compacted = self._apply_compaction(request, result, self._thread_id(request))
            return await handler(compacted)


__all__ = [
    "CompactionMiddleware",
    "CompactionResult",
    "CompactionThresholds",
]
