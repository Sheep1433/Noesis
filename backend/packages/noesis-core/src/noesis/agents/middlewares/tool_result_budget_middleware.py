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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Callable, NotRequired

from deepagents.backends import BackendProtocol
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ExtendedModelResponse,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from noesis.runtime.logging import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable

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
    replacement_reason: str = "tool_result_over_budget"
    replacement_text: str = ""


class ToolResultBudgetState(AgentState[ResponseT]):
    _tool_result_replacements: NotRequired[
        Annotated[dict[str, ReplacementRecord], PrivateStateAttr]
    ]


# State keys this middleware owns; subagent isolation must carry these over.
PRIVATE_STATE_KEYS: tuple[str, ...] = ("_tool_result_replacements",)


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
    tail_text = f"\n{text[-tail:]}" if tail else ""
    return f"{text[:head]}\n… [{omitted} chars omitted] …{tail_text}"


def _approx_tokens(size_chars: int) -> int:
    return size_chars // 4


def _has_artifact_reference(message: ToolMessage) -> bool:
    """True if the result already carries a readable artifact reference."""
    metadata = dict(getattr(message, "additional_kwargs", {}) or {})
    return any(
        bool(metadata.get(key))
        for key in (
            "artifact_path",
            "deepagents_offloaded",
            "large_tool_result",
            "tool_result_offloaded",
            "tool_result_replacement",
        )
    ) or bool(getattr(message, "artifact", None))


class ToolResultBudgetMiddleware(
    AgentMiddleware[ToolResultBudgetState[ResponseT], ContextT, ResponseT]
):
    """Bound oversized tool results with deterministic artifact replacement."""

    state_schema = ToolResultBudgetState

    def __init__(
        self,
        backend: BackendProtocol | None = None,
        *,
        max_chars: int = 24_000,
        aggregate_max_chars: int | None = None,
        artifact_prefix: str = "/large_tool_results",
        head_chars: int = 400,
        tail_chars: int = 200,
        argument_keep_recent_messages: int = 12,
    ) -> None:
        self._backend = backend
        self._max_chars = max(1, max_chars)
        self._aggregate_max_chars = max(
            self._max_chars,
            aggregate_max_chars if aggregate_max_chars is not None else self._max_chars * 2,
        )
        self._artifact_prefix = artifact_prefix
        self._head = max(0, head_chars)
        self._tail = max(0, tail_chars)
        self._argument_keep_recent_messages = max(1, argument_keep_recent_messages)

    @staticmethod
    def _tool_call_id(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("id") or "missing_tool_call_id")

    def _records(self, state: AgentState[Any]) -> dict[str, ReplacementRecord]:
        raw = dict(state.get("_tool_result_replacements", {}))  # type: ignore[arg-type]
        return {
            key: value if isinstance(value, ReplacementRecord) else ReplacementRecord(**value)
            for key, value in raw.items()
        }

    def _offload(self, tool_call_id: str, original_hash: str, text: str) -> str | None:
        """Write content to the backend; return artifact path or None on failure."""
        if self._backend is None:
            return None
        path = f"{self._artifact_prefix}/{tool_call_id}-{original_hash}.txt"
        try:
            result = self._backend.write(path, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "tool result offload failed tool_call_id={} error={}",
                tool_call_id,
                exc,
            )
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
        original_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        artifact_path = None if fallback else self._offload(tool_call_id, original_hash, text)
        synopsis = _synopsis(text, head=self._head, tail=self._tail)
        size = len(text)
        replacement_text = ARTIFACT_REPLACEMENT_TEMPLATE.format(
            artifact_path=artifact_path or "<unavailable — text fallback applied>",
            tool_call_id=tool_call_id,
            original_size=size,
            synopsis=synopsis,
        )
        record = ReplacementRecord(
            tool_call_id=tool_call_id,
            artifact_path=artifact_path,
            synopsis=synopsis,
            original_hash=original_hash,
            original_size=size,
            tokens_freed=max(0, _approx_tokens(size) - _approx_tokens(len(replacement_text))),
            fallback=fallback or artifact_path is None,
            replacement_text=replacement_text,
        )
        metadata = dict(getattr(original, "additional_kwargs", {}) or {})
        metadata["tool_result_replacement"] = True
        if artifact_path is not None:
            metadata["artifact_path"] = artifact_path
        # Preserve identity + status + category + outcome metadata.
        new_message = ToolMessage(
            content=replacement_text,
            tool_call_id=original.tool_call_id,
            name=original.name,
            id=original.id,
            artifact=original.artifact,
            status=getattr(original, "status", None),
            additional_kwargs=metadata,
            response_metadata=dict(getattr(original, "response_metadata", {}) or {}),
        )
        return new_message, record

    @staticmethod
    def _replay(original: ToolMessage, record: ReplacementRecord) -> ToolMessage:
        replacement_text = record.replacement_text or ARTIFACT_REPLACEMENT_TEMPLATE.format(
            artifact_path=record.artifact_path or "<unavailable — text fallback applied>",
            tool_call_id=record.tool_call_id,
            original_size=record.original_size,
            synopsis=record.synopsis,
        )
        metadata = dict(getattr(original, "additional_kwargs", {}) or {})
        metadata["tool_result_replacement"] = True
        if record.artifact_path is not None:
            metadata["artifact_path"] = record.artifact_path
        return ToolMessage(
            content=replacement_text,
            tool_call_id=original.tool_call_id,
            name=original.name,
            id=original.id,
            artifact=original.artifact,
            status=getattr(original, "status", None),
            additional_kwargs=metadata,
            response_metadata=dict(getattr(original, "response_metadata", {}) or {}),
        )

    def _replace_message(
        self,
        message: ToolMessage,
        records: dict[str, ReplacementRecord],
        *,
        force: bool = False,
    ) -> tuple[ToolMessage, bool]:
        if _has_artifact_reference(message) or (
            not force and _content_size(message.content) <= self._max_chars
        ):
            return message, False
        text = _content_text(message.content)
        if text is None:
            return message, False
        tool_call_id = str(message.tool_call_id or "missing_tool_call_id")
        original_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        existing = records.get(tool_call_id)
        if existing is not None and existing.original_hash == original_hash:
            return self._replay(message, existing), False
        replacement, record = self._build_replacement(message, tool_call_id, text, fallback=False)
        records[tool_call_id] = record
        return replacement, True

    def _project_tool_messages(
        self,
        messages: list[ToolMessage],
        records: dict[str, ReplacementRecord],
    ) -> tuple[list[ToolMessage], bool]:
        """Apply per-result and aggregate budgets to one parallel result batch."""
        projected: list[ToolMessage] = []
        created = False
        for message in messages:
            replacement, was_created = self._replace_message(message, records)
            projected.append(replacement)
            created = created or was_created

        original_sizes = [_content_size(message.content) for message in messages]
        remaining = sum(
            size
            for message, size in zip(projected, original_sizes, strict=True)
            if not _has_artifact_reference(message)
        )
        if remaining <= self._aggregate_max_chars:
            return projected, created

        candidates = sorted(
            (
                (size, index)
                for index, (message, size) in enumerate(
                    zip(projected, original_sizes, strict=True)
                )
                if not _has_artifact_reference(message)
            ),
            reverse=True,
        )
        for size, index in candidates:
            replacement, was_created = self._replace_message(
                messages[index],
                records,
                force=True,
            )
            projected[index] = replacement
            created = created or was_created
            remaining -= size
            if remaining <= self._aggregate_max_chars:
                break
        return projected, created

    def _replace_tool_arguments(
        self,
        message: AIMessage,
        records: dict[str, ReplacementRecord],
    ) -> tuple[AIMessage, bool]:
        calls = [dict(call) for call in message.tool_calls]
        created = False
        changed = False
        for call in calls:
            args = call.get("args")
            call_id = str(call.get("id") or "missing_tool_call_id")
            if not isinstance(args, dict):
                continue
            bounded_args = dict(args)
            for field in ("content", "new_string"):
                text = bounded_args.get(field)
                if not isinstance(text, str) or len(text) <= self._max_chars:
                    continue
                original_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
                record_key = f"arg:{call_id}:{field}"
                existing = records.get(record_key)
                if existing is not None and existing.original_hash == original_hash:
                    bounded_args[field] = existing.replacement_text
                    changed = True
                    continue
                artifact_path = self._offload(record_key, original_hash, text)
                synopsis = _synopsis(text, head=min(self._head, 200), tail=0)
                replacement = (
                    f"[large {field} omitted; artifact_path={artifact_path or '<unavailable>'}; "
                    f"original_hash={original_hash}; original_size_chars={len(text)}]\n"
                    f"{synopsis}"
                )
                records[record_key] = ReplacementRecord(
                    tool_call_id=call_id,
                    artifact_path=artifact_path,
                    synopsis=synopsis,
                    original_hash=original_hash,
                    original_size=len(text),
                    tokens_freed=max(
                        0,
                        _approx_tokens(len(text)) - _approx_tokens(len(replacement)),
                    ),
                    fallback=artifact_path is None,
                    replacement_reason="tool_argument_over_budget",
                    replacement_text=replacement,
                )
                bounded_args[field] = replacement
                created = True
                changed = True
            call["args"] = bounded_args
        if not changed:
            return message, False
        return message.model_copy(update={"tool_calls": calls}), created

    def _maybe_replace(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
        state: AgentState[Any],
    ) -> tuple[ToolMessage | Command[Any], Command[Any] | None]:
        if isinstance(result, Command):
            if not isinstance(result.update, dict):
                return result, None
            messages = result.update.get("messages")
            if not isinstance(messages, list):
                return result, None
            records = self._records(state)
            tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
            bounded, created = self._project_tool_messages(tool_messages, records)
            replacements = iter(bounded)
            projected = [next(replacements) if isinstance(message, ToolMessage) else message for message in messages]
            changed = any(before is not after for before, after in zip(messages, projected, strict=True))
            if not changed:
                return result, None
            command = Command(
                graph=result.graph,
                update={**result.update, "messages": projected},
                resume=result.resume,
                goto=result.goto,
            )
            update = Command(update={"_tool_result_replacements": records}) if created else None
            return command, update
        if not isinstance(result, ToolMessage):
            return result, None
        if _has_artifact_reference(result):
            return result, None
        records = self._records(state)
        replacement, created = self._replace_message(result, records)
        update = Command(update={"_tool_result_replacements": records}) if created else None
        return replacement, update

    def _project_history(
        self, request: ModelRequest[ContextT]
    ) -> tuple[ModelRequest[ContextT], dict[str, ReplacementRecord] | None]:
        records = self._records(request.state)
        created = False
        projected: list[AnyMessage] = []
        index = 0
        messages = list(request.messages)
        argument_cutoff = max(0, len(messages) - self._argument_keep_recent_messages)
        while index < len(messages):
            if not isinstance(messages[index], ToolMessage):
                message = messages[index]
                if isinstance(message, AIMessage) and index < argument_cutoff:
                    message, argument_created = self._replace_tool_arguments(message, records)
                    created = created or argument_created
                projected.append(message)
                index += 1
                continue
            end = index
            while end < len(messages) and isinstance(messages[end], ToolMessage):
                end += 1
            bounded, batch_created = self._project_tool_messages(messages[index:end], records)
            projected.extend(bounded)
            created = created or batch_created
            index = end
        changed = any(before is not after for before, after in zip(messages, projected, strict=True))
        if not changed:
            projected_request = request
        elif created:
            projected_request = request.override(
                messages=projected,
                state={
                    **request.state,
                    "_tool_result_replacements": records,
                },
            )
        else:
            projected_request = request.override(messages=projected)
        return projected_request, records if created else None

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        """Project both resumed and in-turn historical tool results."""
        return self._project_history(request)[0]

    @staticmethod
    def _with_records(
        result: ModelCallResult,
        records: dict[str, ReplacementRecord] | None,
    ) -> ModelCallResult:
        if records is None:
            return result
        if isinstance(result, ExtendedModelResponse):
            response = result.model_response
            existing = result.command
        elif isinstance(result, AIMessage):
            response = ModelResponse(result=[result])
            existing = None
        else:
            response = result
            existing = None
        update = {"_tool_result_replacements": records}
        if existing is not None and isinstance(existing.update, dict):
            update = {**existing.update, **update}
        return ExtendedModelResponse(
            model_response=response,
            command=Command(
                graph=existing.graph if existing is not None else None,
                update=update,
                resume=existing.resume if existing is not None else None,
                goto=existing.goto if existing is not None else (),
            ),
        )

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelCallResult:
        projected, records = self._project_history(request)
        return self._with_records(handler(projected), records)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelCallResult:
        projected, records = self._project_history(request)
        return self._with_records(await handler(projected), records)

    def _merge_replacement_with_update(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        replacement, update = self._maybe_replace(request, result, request.state)
        if update is None:
            return replacement
        # Carry the private-state update alongside the bounded result.
        if isinstance(replacement, Command):
            return Command(
                graph=replacement.graph,
                update={**replacement.update, **update.update},
                resume=replacement.resume,
                goto=replacement.goto,
            )
        return Command(update={**update.update, "messages": [replacement]})  # type: ignore[arg-type]

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return self._merge_replacement_with_update(request, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        return self._merge_replacement_with_update(request, await handler(request))


__all__ = [
    "ARTIFACT_REPLACEMENT_TEMPLATE",
    "PRIVATE_STATE_KEYS",
    "ReplacementRecord",
    "ToolResultBudgetState",
    "ToolResultBudgetMiddleware",
]
