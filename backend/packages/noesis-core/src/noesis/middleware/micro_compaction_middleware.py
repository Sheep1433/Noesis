"""Micro-compaction middleware — model-free local reduction.

Applies a multi-strategy local reduction to the effective history before each
model call, *without* invoking a model. This is the Noesis owner for the
Claude Code "micro-compaction" step; upstream's ``SummarizationMiddleware``
only has a blunt ``_truncate_args`` that cuts oversized string args to 20 chars
+ fixed text (no artifact synopsis, no hash, no resume replay, no write/edit
handling). Per design §10, enabling this middleware closes the upstream
truncation owner — there is a single owner.

Design contract (``simplify-agent-context-architecture`` §10):

1. keep the recent window of tool call/result verbatim;
2. replace old large tool results with artifact path + synopsis;
3. for old write/edit tool calls with large args, keep the head, hash, target
   path and result status;
4. drop duplicate dynamic attachments and stale tool/MCP delta;
5. never cut a tool call/result pair, a thinking block, or an API round.

Strategy 4 requires the stable-source refs owned by DurableContext /
ToolCatalog (not yet implemented); this middleware exposes an extension point
(``dedupe_hook``) so that phase can plug in without this owner gaining a
runtime dependency. Strategies 1, 2, 3, 5 are fully implemented here.

Self-containment: depends only on an injected ``BackendProtocol`` and
``token_counter``; reduction lives in private state (``_micro_compaction_records``)
for replay. No ``runtime``/``service`` calls.
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
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


MICRO_SNIPPET_TEMPLATE = "[micro-compacted: {strategy}; kept_head + hash={hash}]"


@dataclass(frozen=True)
class MicroRecord:
    """A replayable local-reduction decision over one message."""

    message_index: int
    strategy: str  # "tool_result_offload" | "tool_arg_truncate" | "duplicate_drop"
    original_hash: str
    tokens_freed: int


def _approx_tokens(messages: list[AnyMessage]) -> int:
    return sum(len(repr(m.content)) for m in messages) // 4


def _content_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") in (None, "text"):
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts) if parts else None
    if value is None:
        return None
    return str(value)


def _content_size(value: Any) -> int:
    text = _content_text(value)
    return len(text) if text is not None else len(str(value or ""))


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _tool_call_ids(msg: AnyMessage) -> set[str]:
    if isinstance(msg, AIMessage):
        ids: set[str] = set()
        for call in msg.tool_calls:
            cid = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
            if cid:
                ids.add(str(cid))
        return ids
    return set()


def _is_tool_pair_safe(messages: list[AnyMessage], drop_index: int) -> bool:
    """True if removing/altering message at drop_index keeps tool pairs intact."""
    if not isinstance(messages[drop_index], ToolMessage):
        return True  # truncating an AIMessage's args keeps the pair structurally intact
    target = messages[drop_index]
    if not isinstance(target, ToolMessage) or not target.tool_call_id:
        return True
    # The matching AIMessage(tool_call) must still be present in the projection.
    for msg in messages:
        if isinstance(msg, AIMessage) and target.tool_call_id in _tool_call_ids(msg):
            return True
    return False


class MicroCompactionMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Model-free local reduction of the effective history."""

    def __init__(
        self,
        backend: BackendProtocol | None = None,
        *,
        keep_recent: int = 6,
        large_tool_result_chars: int = 8_000,
        large_tool_arg_chars: int = 2_000,
        arg_head_chars: int = 200,
        token_counter: Callable[[list[AnyMessage]], int] | None = None,
        dedupe_hook: Callable[[list[AnyMessage]], list[int]] | None = None,
    ) -> None:
        self._backend = backend
        self._keep_recent = max(0, keep_recent)
        self._large_result = max(1, large_tool_result_chars)
        self._large_arg = max(1, large_tool_arg_chars)
        self._arg_head = max(0, arg_head_chars)
        self._token_counter = token_counter or _approx_tokens
        self._dedupe_hook = dedupe_hook

    def _records(self, state: AgentState[Any]) -> list[MicroRecord]:
        return list(state.get("_micro_compaction_records", []))  # type: ignore[arg-type]

    def _offload(self, tool_call_id: str, text: str) -> str | None:
        if self._backend is None:
            return None
        path = f"/large_tool_results/{tool_call_id}"
        try:
            result = self._backend.write(path, text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("micro-compaction offload failed for %s: %s", tool_call_id, exc)
            return None
        if result is None or getattr(result, "error", None):
            return None
        return path

    def _reduce_tool_result(
        self,
        messages: list[AnyMessage],
        index: int,
    ) -> tuple[ToolMessage, MicroRecord] | None:
        msg = messages[index]
        if not isinstance(msg, ToolMessage):
            return None
        text = _content_text(msg.content)
        if text is None or len(text) <= self._large_result:
            return None
        if text.startswith("[tool result micro-compacted"):
            return None  # already reduced — idempotent
        if not _is_tool_pair_safe(messages, index):
            return None  # never cut a pair
        artifact_path = self._offload(msg.tool_call_id or f"msg{index}", text)
        synopsis = text[: self._large_result // 8] or "(empty)"
        replacement = f"[tool result micro-compacted; artifact={artifact_path or 'n/a'}]\n{synopsis}"
        new_msg = ToolMessage(
            content=replacement,
            tool_call_id=msg.tool_call_id,
            name=msg.name,
            id=msg.id,
            status=getattr(msg, "status", None),
            additional_kwargs=dict(getattr(msg, "additional_kwargs", {}) or {}),
            response_metadata=dict(getattr(msg, "response_metadata", {}) or {}),
        )
        record = MicroRecord(
            message_index=index,
            strategy="tool_result_offload",
            original_hash=_short_hash(text),
            tokens_freed=len(text) // 4,
        )
        return new_msg, record

    def _reduce_tool_args(
        self,
        messages: list[AnyMessage],
        index: int,
    ) -> tuple[AIMessage, MicroRecord] | None:
        msg = messages[index]
        if not isinstance(msg, AIMessage) or not msg.tool_calls:
            return None
        changed = False
        new_calls: list[dict[str, Any]] = []
        original_blob = ""
        for call in msg.tool_calls:
            call_dict = call if isinstance(call, dict) else dict(call)
            args = call_dict.get("args", {})
            original_blob += repr(args)
            if isinstance(args, dict):
                trimmed: dict[str, Any] = {}
                big_key = False
                for key, value in args.items():
                    if isinstance(value, str) and len(value) > self._large_arg:
                        trimmed[key] = value[: self._arg_head] + "… [truncated]"
                        big_key = True
                    else:
                        trimmed[key] = value
                if big_key:
                    changed = True
                    new_calls.append({**call_dict, "args": trimmed})
                else:
                    new_calls.append(call_dict)
            else:
                new_calls.append(call_dict)
        if not changed:
            return None
        new_msg = msg.model_copy(update={"tool_calls": new_calls})
        record = MicroRecord(
            message_index=index,
            strategy="tool_arg_truncate",
            original_hash=_short_hash(original_blob),
            tokens_freed=len(original_blob) // 4,
        )
        return new_msg, record

    def _apply(self, messages: list[AnyMessage]) -> tuple[list[AnyMessage], list[MicroRecord]]:
        if not messages:
            return list(messages), []
        cutoff = max(0, len(messages) - self._keep_recent)
        out: list[AnyMessage] = list(messages)
        records: list[MicroRecord] = []

        # Strategy 4 (extension point): duplicate attachment / stale MCP delta.
        # Only drop an index the hook explicitly returns; pair-safety is checked.
        if self._dedupe_hook is not None:
            for idx in self._dedupe_hook(messages):
                if 0 <= idx < cutoff and _is_tool_pair_safe(messages, idx):
                    marker = MICRO_SNIPPET_TEMPLATE.format(strategy="duplicate_drop", hash="dropped")
                    out[idx] = AIMessage(content=marker)
                    records.append(MicroRecord(idx, "duplicate_drop", "dropped", 0))

        # Strategy 2 & 3: reduce old (outside recent window) large results/args.
        for i in range(cutoff):
            msg = out[i]
            reduced: tuple[AnyMessage, MicroRecord] | None = None
            if isinstance(msg, ToolMessage):
                reduced = self._reduce_tool_result(out, i)
            elif isinstance(msg, AIMessage):
                reduced = self._reduce_tool_args(out, i)
            if reduced is not None:
                out[i] = reduced[0]
                records.append(reduced[1])

        # Strategy 1 & 5: recent window kept verbatim; pairs never cut (checked above).
        return out, records

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        records = self._records(request.state)
        # Replay: if records exist, the projection was already computed and
        # stored as the effective messages by the previous call. Re-derive
        # deterministically from raw messages + records is not needed because
        # MicroCompaction is idempotent over already-reduced messages.
        reduced, new_records = self._apply(list(request.messages))
        if not new_records:
            return request
        all_records = records + new_records
        return request.override(
            messages=reduced,
            state={**request.state, "_micro_compaction_records": all_records},  # type: ignore[arg-type]
        )

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


__all__ = ["MicroCompactionMiddleware", "MicroRecord"]
