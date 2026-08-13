"""Tool result budget middleware — deterministic content replacement.

Replaces oversized tool results with an artifact reference + synopsis before
they enter the effective history, preserving ``status``, ``errorCategory``,
``outcome`` and ``tool_call_id``. This is the Noesis owner for the Claude Code
deterministic tool-result replacement step; upstream's eviction helper
(``_offload_tool_message_content``) only preserves ``status``/``artifact``
and does not keep ``errorCategory``/``outcome`` or a replayable replacement
record.

Design contract (``simplify-agent-context-architecture`` §14 +
``agent-tool-failure-handling`` spec delta):

- replacement happens before the result enters effective history;
- a replacement record (artifact path, synopsis, original hash, tokens freed)
  is persisted in private state so checkpoint resume replays the same decision;
- the replacement preserves ``status``, ``errorCategory``, ``outcome`` and the
  original ``tool_call_id``;
- a result that already carries an artifact reference is kept as-is (no
  re-offload / re-truncation);
- when the injected backend offload fails, a final bounded text fallback is
  used — but ``status``/``category``/``outcome`` are never rewritten.

Self-containment: depends only on an injected ``BackendProtocol`` and
LangGraph private state. No ``runtime``/``service`` calls.
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
    ResponseT,
)
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


ARTIFACT_REPLACEMENT_TEMPLATE = """Tool result too large and was offloaded to an artifact.

artifact_path: {artifact_path}
tool_call_id: {tool_call_id}
original_size_chars: {original_size}
synopsis:
{synopsis}

Use the artifact path to read the full result in chunks."""


@dataclass(frozen=True)
class ReplacementRecord:
    """A persisted replacement decision, replayable on resume."""

    tool_call_id: str
    artifact_path: str | None
    synopsis: str
    original_hash: str
    original_size: int
    tokens_freed: int
    fallback: bool  # True when text fallback was used (backend offload failed)


def _content_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(
            _content_size(item.get("text", "")) if isinstance(item, dict) else _content_size(item)
            for item in value
        )
    return len(str(value or ""))


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


def _synopsis(text: str, *, head: int = 400, tail: int = 200) -> str:
    """Deterministic head+tail synopsis with a truncation marker."""
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n… [{omitted} chars omitted] …\n{text[-tail:]}"


def _approx_tokens(size_chars: int) -> int:
    return size_chars // 4


def _has_artifact_reference(message: ToolMessage) -> bool:
    """True if the result already carries a readable artifact reference."""
    metadata = dict(getattr(message, "additional_kwargs", {}) or {})
    return any(
        bool(metadata.get(key))
        for key in ("artifact_path", "deepagents_offloaded", "large_tool_result", "tool_result_offloaded")
    ) or bool(getattr(message, "artifact", None))


class ToolResultBudgetMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Bound oversized tool results with deterministic artifact replacement."""

    def __init__(
        self,
        backend: BackendProtocol | None = None,
        *,
        max_chars: int = 24_000,
        artifact_prefix: str = "/large_tool_results",
        head_chars: int = 400,
        tail_chars: int = 200,
    ) -> None:
        self._backend = backend
        self._max_chars = max(1, max_chars)
        self._artifact_prefix = artifact_prefix
        self._head = max(0, head_chars)
        self._tail = max(0, tail_chars)

    @staticmethod
    def _tool_call_id(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("id") or "missing_tool_call_id")

    def _records(self, state: AgentState[Any]) -> dict[str, ReplacementRecord]:
        return dict(state.get("_tool_result_replacements", {}))  # type: ignore[arg-type]

    def _offload(self, tool_call_id: str, text: str) -> str | None:
        """Write content to the backend; return artifact path or None on failure."""
        if self._backend is None:
            return None
        path = f"{self._artifact_prefix}/{tool_call_id}"
        try:
            result = self._backend.write(path, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool result offload failed for %s: %s", tool_call_id, exc)
            return None
        if result is None or getattr(result, "error", None):
            return None
        return path

    def _build_replacement(
        self,
        original: ToolMessage,
        tool_call_id: str,
        text: str,
        *,
        fallback: bool,
    ) -> tuple[ToolMessage, ReplacementRecord]:
        artifact_path = None if fallback else self._offload(tool_call_id, text)
        synopsis = _synopsis(text, head=self._head, tail=self._tail)
        size = len(text)
        record = ReplacementRecord(
            tool_call_id=tool_call_id,
            artifact_path=artifact_path,
            synopsis=synopsis,
            original_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            original_size=size,
            tokens_freed=_approx_tokens(size),
            fallback=fallback or artifact_path is None,
        )
        replacement_text = ARTIFACT_REPLACEMENT_TEMPLATE.format(
            artifact_path=artifact_path or "<unavailable — text fallback applied>",
            tool_call_id=tool_call_id,
            original_size=size,
            synopsis=synopsis,
        )
        # Preserve identity + status + category + outcome metadata.
        new_message = ToolMessage(
            content=replacement_text,
            tool_call_id=original.tool_call_id,
            name=original.name,
            id=original.id,
            artifact=original.artifact,
            status=getattr(original, "status", None),
            additional_kwargs=dict(getattr(original, "additional_kwargs", {}) or {}),
            response_metadata=dict(getattr(original, "response_metadata", {}) or {}),
        )
        return new_message, record

    def _maybe_replace(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
        state: AgentState[Any],
    ) -> tuple[ToolMessage | Command[Any], Command[Any] | None]:
        if not isinstance(result, ToolMessage):
            return result, None
        if _has_artifact_reference(result):
            return result, None
        size = _content_size(result.content)
        if size <= self._max_chars:
            return result, None
        tool_call_id = self._tool_call_id(request)
        records = self._records(state)
        existing = records.get(tool_call_id)
        if existing is not None:
            # Replay the same decision on resume — rebuild the replacement text
            # from the stored record so the projection is byte-stable.
            replacement_text = ARTIFACT_REPLACEMENT_TEMPLATE.format(
                artifact_path=existing.artifact_path or "<unavailable — text fallback applied>",
                tool_call_id=tool_call_id,
                original_size=existing.original_size,
                synopsis=existing.synopsis,
            )
            replayed = ToolMessage(
                content=replacement_text,
                tool_call_id=result.tool_call_id,
                name=result.name,
                id=result.id,
                artifact=result.artifact,
                status=getattr(result, "status", None),
                additional_kwargs=dict(getattr(result, "additional_kwargs", {}) or {}),
                response_metadata=dict(getattr(result, "response_metadata", {}) or {}),
            )
            return replayed, None
        text = _content_text(result.content)
        if text is None:
            # Non-text content we cannot bound deterministically — keep as-is.
            return result, None
        replacement, record = self._build_replacement(result, tool_call_id, text, fallback=False)
        updated_records = {**records, tool_call_id: record}
        update = Command(update={"_tool_result_replacements": updated_records})
        return replacement, update

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        replacement, update = self._maybe_replace(request, result, request.state)
        if update is not None:
            # Carry the private-state update alongside the bounded result.
            return Command(update={**update.update, "messages": [replacement]})  # type: ignore[arg-type]
        return replacement

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        replacement, update = self._maybe_replace(request, result, request.state)
        if update is not None:
            return Command(update={**update.update, "messages": [replacement]})  # type: ignore[arg-type]
        return replacement


__all__ = [
    "ARTIFACT_REPLACEMENT_TEMPLATE",
    "ReplacementRecord",
    "ToolResultBudgetMiddleware",
]
