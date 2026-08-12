"""Snip middleware — explicit effective-history projection.

Removes explicitly-selected content from the *effective* history the model
sees, without physically deleting the raw transcript. This is the Noesis owner
for the Claude Code "snip" projection step; upstream has no equivalent.

Design contract (``simplify-agent-context-architecture`` §9):

- accepts an explicit message/block selector — never guesses targets from the
  prompt;
- only changes the effective-history projection; the raw transcript / checkpoint
  messages are never physically deleted;
- each replacement records a marker, the reason, the original content hash and
  ``tokens_freed``;
- after checkpoint resume the same projection is replayed (projections live in
  LangGraph private state, keyed by selector);
- must NOT snip a compaction boundary, the current user request, or half of a
  tool call/result pair.

Self-containment: depends only on injected selectors and LangGraph private
state. No ``runtime``/``service`` calls.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    ToolMessage,
)
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from collections.abc import Awaitable


SNIP_MARKER_TEMPLATE = "[snipped: {reason}; original_hash={hash}; tokens_freed~{tokens}]"


class SnipError(ValueError):
    """Raised when a snip selector violates an integrity constraint."""


@dataclass(frozen=True)
class SnipSelector:
    """Selects a contiguous half-open range of messages to snip.

    ``start`` is inclusive, ``stop`` exclusive — slicing semantics over the
    effective message list at the moment snip is requested. The caller is
    responsible for computing these indices from explicit user/system intent;
    this middleware never derives them from prompt content.
    """

    start: int
    stop: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.stop < 0:
            raise SnipError("snip selectors must be non-negative")
        if self.stop <= self.start:
            raise SnipError("snip selector stop must be greater than start")


@dataclass(frozen=True)
class SnipRecord:
    """A persisted projection decision, replayable on resume."""

    selector: SnipSelector
    reason: str
    original_hash: str
    tokens_freed: int


class _SnipState(TypedDict, total=False):
    """Private state holding the projection ledger.

    Marked private (leading underscore) so it is excluded from agent output
    and sub-agent inheritance by convention; the field is also namespaced to
    avoid collision with other middleware.
    """

    _snip_records: list[SnipRecord]


def _content_hash(message: AnyMessage) -> str:
    """Stable hash of a message's content for replay identity."""
    payload = repr(message.content)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _approx_tokens(messages: list[AnyMessage]) -> int:
    """Cheap token proxy (chars/4). Only used for the recorded ``tokens_freed``."""
    total = 0
    for msg in messages:
        total += len(repr(msg.content))
    return total // 4


def _is_tool_pair_boundary_violation(
    messages: list[AnyMessage],
    start: int,
    stop: int,
) -> bool:
    """True if snipping [start, stop) would split a tool call/result pair.

    A pair is split when the snip leaves an ``AIMessage`` with a tool call
    whose matching ``ToolMessage`` falls inside the snipped range (or vice
    versa), or when the snip ends between a tool call and its result.
    """
    kept = messages[:start] + messages[stop:]
    kept_call_ids: set[str] = set()
    kept_result_ids: set[str] = set()
    for msg in kept:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls:
                cid = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
                if cid:
                    kept_call_ids.add(str(cid))
        elif isinstance(msg, ToolMessage):
            if msg.tool_call_id:
                kept_result_ids.add(msg.tool_call_id)
    # A call without its result, or a result without its call, inside the kept
    # tail means the pair was severed.
    return bool(kept_call_ids - kept_result_ids) or bool(kept_result_ids - kept_call_ids)


def apply_snip_projection(
    messages: list[AnyMessage],
    records: list[SnipRecord],
) -> list[AnyMessage]:
    """Return the effective history with snipped ranges replaced by markers.

    Projections are applied in order; each record's selector is resolved
    against the message list *before* earlier projections mutate it — i.e.
    selectors always index the raw transcript. This keeps replay deterministic
    across resume.
    """
    if not records:
        return list(messages)
    snipped_ranges: list[tuple[int, int, SnipRecord]] = []
    for record in records:
        snipped_ranges.append((record.selector.start, record.selector.stop, record))
    snipped_ranges.sort(key=lambda r: r[0])

    out: list[AnyMessage] = []
    cursor = 0
    for start, stop, record in snipped_ranges:
        if start < cursor:
            # overlapping/unordered selector — skip rather than corrupt projection
            continue
        out.extend(messages[cursor:start])
        marker_text = SNIP_MARKER_TEMPLATE.format(
            reason=record.reason,
            hash=record.original_hash,
            tokens=record.tokens_freed,
        )
        out.append(HumanMessage(content=marker_text))
        cursor = stop
    out.extend(messages[cursor:])
    return out


class SnipMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Apply explicit snip projections to the effective model request.

    Projections are accumulated in private state (``_snip_records``) so that
    checkpoint resume replays them identically. The middleware does not snip
    on its own — it only honours selectors pushed via :meth:`request_snip` from
    explicit user/system action.
    """

    def __init__(self, *, token_counter: Callable[[list[AnyMessage]], int] | None = None) -> None:
        self._token_counter = token_counter or _approx_tokens

    # -- projection ledger (private state) ------------------------------

    @staticmethod
    def _records(state: AgentState[Any]) -> list[SnipRecord]:
        return list(state.get("_snip_records", []))  # type: ignore[arg-type]

    def request_snip(
        self,
        messages: list[AnyMessage],
        selector: SnipSelector,
        reason: str,
    ) -> SnipRecord:
        """Validate a selector against the raw transcript and return a record.

        Integrity constraints enforced here (design §9):
        - cannot snip the trailing user message (current request);
        - cannot split a tool call/result pair.
        Compaction-boundary protection is enforced by Compaction owning its own
        boundary marker; Snip refuses to snip a range that contains the most
        recent HumanMessage.
        """
        if not messages:
            raise SnipError("cannot snip an empty transcript")
        if selector.stop > len(messages):
            raise SnipError("snip selector exceeds transcript length")
        # Protect the current user request: the last human message (and
        # everything after it) must remain visible.
        last_human = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                last_human = i
                break
        if last_human >= 0 and selector.start <= last_human < selector.stop:
            raise SnipError("snip must not remove the current user request")
        if _is_tool_pair_boundary_violation(messages, selector.start, selector.stop):
            raise SnipError("snip would split a tool call/result pair")

        snipped = messages[selector.start : selector.stop]
        record = SnipRecord(
            selector=selector,
            reason=reason,
            original_hash=hashlib.sha256(
                "\n".join(_content_hash(m) for m in snipped).encode("utf-8"),
            ).hexdigest()[:16],
            tokens_freed=self._token_counter(snipped),
        )
        return record

    def attach_record(self, state: AgentState[Any], record: SnipRecord) -> None:
        """Persist a projection decision into private state for replay."""
        records: list[SnipRecord] = self._records(state)
        records.append(record)
        state["_snip_records"] = records  # type: ignore[assignment]

    # -- model-call seam ------------------------------------------------

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        records = self._records(request.state)
        if not records:
            return request
        projected = apply_snip_projection(list(request.messages), records)
        return request.override(messages=projected)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return handler(self.modify_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        return await handler(self.modify_request(request))


__all__ = [
    "SNIP_MARKER_TEMPLATE",
    "SnipError",
    "SnipMiddleware",
    "SnipRecord",
    "SnipSelector",
    "apply_snip_projection",
]
