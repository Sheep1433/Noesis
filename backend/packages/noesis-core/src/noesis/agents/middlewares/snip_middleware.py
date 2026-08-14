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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Callable, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langchain_core.tools.base import InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

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
    message_keys: tuple[str, ...] = ()


class SnipState(AgentState[ResponseT]):
    """Checkpointed projection ledger hidden from agent input/output."""

    _snip_records: NotRequired[Annotated[list[SnipRecord], PrivateStateAttr]]


def _content_hash(message: AnyMessage) -> str:
    """Stable hash of a message's content for replay identity."""
    payload = repr(message.content)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _message_fingerprint(message: AnyMessage) -> str:
    payload = (
        message.type,
        message.id,
        repr(message.content),
        getattr(message, "tool_call_id", None),
        repr(getattr(message, "tool_calls", None)),
    )
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:16]


def _indexed_message_keys(messages: list[AnyMessage]) -> list[str]:
    """Return stable content identities disambiguated by occurrence."""
    seen: dict[str, int] = {}
    keys: list[str] = []
    for message in messages:
        fingerprint = _message_fingerprint(message)
        occurrence = seen.get(fingerprint, 0)
        seen[fingerprint] = occurrence + 1
        keys.append(f"{fingerprint}:{occurrence}")
    return keys


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
    call_positions: dict[str, set[int]] = {}
    result_positions: dict[str, set[int]] = {}
    for index, msg in enumerate(messages):
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls:
                cid = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
                if cid:
                    call_positions.setdefault(str(cid), set()).add(index)
        elif isinstance(msg, ToolMessage):
            if msg.tool_call_id:
                result_positions.setdefault(msg.tool_call_id, set()).add(index)
    selected = set(range(start, stop))
    for call_id in call_positions.keys() & result_positions.keys():
        call_selected = bool(call_positions[call_id] & selected)
        result_selected = bool(result_positions[call_id] & selected)
        if call_selected != result_selected:
            return True
    return False


def _is_compaction_boundary(message: AnyMessage) -> bool:
    metadata = dict(getattr(message, "additional_kwargs", {}) or {})
    return (
        metadata.get("lc_source") == "summarization"
        or bool(metadata.get("boundary_hash"))
        or bool(metadata.get("compaction_boundary"))
    )


def _resolve_selector(messages: list[AnyMessage], record: SnipRecord) -> tuple[int, int] | None:
    if not record.message_keys:
        if record.selector.stop <= len(messages):
            return record.selector.start, record.selector.stop
        return None
    keys = _indexed_message_keys(messages)
    width = len(record.message_keys)
    needle = list(record.message_keys)
    for start in range(0, len(keys) - width + 1):
        if keys[start : start + width] == needle:
            return start, start + width
    return None


def _last_human_index(messages: list[AnyMessage]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return index
    return -1


def _projection_range_is_safe(messages: list[AnyMessage], start: int, stop: int) -> bool:
    last_human = _last_human_index(messages)
    return (
        (last_human < 0 or stop <= last_human)
        and not any(_is_compaction_boundary(message) for message in messages[start:stop])
        and not _is_tool_pair_boundary_violation(messages, start, stop)
    )


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
    for raw_record in records:
        record = raw_record
        if isinstance(raw_record, dict):
            selector = raw_record["selector"]
            if isinstance(selector, dict):
                selector = SnipSelector(**selector)
            record = SnipRecord(**{**raw_record, "selector": selector})
        resolved = _resolve_selector(messages, record)
        if resolved is not None and _projection_range_is_safe(messages, *resolved):
            snipped_ranges.append((*resolved, record))
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


class SnipMiddleware(AgentMiddleware[SnipState[ResponseT], ContextT, ResponseT]):
    """Apply explicit snip projections to the effective model request.

    Projections are accumulated in private state (``_snip_records``) so that
    checkpoint resume replays them identically. The middleware does not snip
    on its own — it only honours selectors pushed via :meth:`request_snip` from
    explicit user/system action.
    """

    state_schema = SnipState

    def __init__(self, *, token_counter: Callable[[list[AnyMessage]], int] | None = None) -> None:
        self._token_counter = token_counter or _approx_tokens
        self.tools = (self._create_snip_tool(),)

    def _create_snip_tool(self) -> StructuredTool:
        middleware = self

        def snip_context(
            start: int,
            stop: int,
            reason: str,
            state: Annotated[dict[str, Any], InjectedState],
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> Command[Any]:
            """Hide an explicit old message range from future model context."""
            record = middleware.request_snip(
                list(state.get("messages", [])), SnipSelector(start, stop), reason
            )
            records = middleware._records(state)
            records.append(record)
            return Command(
                update={
                    "_snip_records": records,
                    "messages": [
                        ToolMessage(
                            content=f"Snipped messages [{start}, {stop}) from effective context.",
                            name="snip_context",
                            tool_call_id=tool_call_id,
                        )
                    ],
                }
            )

        return StructuredTool.from_function(
            func=snip_context,
            name="snip_context",
            description=(
                "Hide an explicit old message range from future model context without "
                "deleting the raw transcript. Never target the current user turn."
            ),
        )

    # -- projection ledger (private state) ------------------------------

    @staticmethod
    def _records(state: AgentState[Any]) -> list[SnipRecord]:
        records: list[SnipRecord] = []
        for value in state.get("_snip_records", []):  # type: ignore[union-attr]
            if isinstance(value, SnipRecord):
                records.append(value)
                continue
            selector = value["selector"]
            if isinstance(selector, dict):
                selector = SnipSelector(**selector)
            records.append(SnipRecord(**{**value, "selector": selector}))
        return records

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
        # Protect the current request turn: the last human message and all
        # messages produced after it must remain visible.
        last_human = _last_human_index(messages)
        if last_human >= 0 and selector.stop > last_human:
            raise SnipError("snip must not remove the current user request")
        if any(_is_compaction_boundary(message) for message in messages[selector.start : selector.stop]):
            raise SnipError("snip must not remove a compaction boundary")
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
            message_keys=tuple(_indexed_message_keys(messages)[selector.start : selector.stop]),
        )
        return record

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
    "SnipState",
    "apply_snip_projection",
]
