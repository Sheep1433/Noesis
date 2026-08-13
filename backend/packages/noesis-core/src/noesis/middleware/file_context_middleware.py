"""File context middleware — model-facing read state and stale hints.

Maintains a bounded LRU of files the agent has read (path, mtime/hash, range,
last access) and injects stale-file hints into the model request. This is the
Noesis owner for the Claude Code "file context" behaviour; upstream
``FilesystemMiddleware`` only has a "must read before edit" prompt hint with
no mtime/hash staleness detection.

Design contract (``simplify-agent-context-architecture`` §8):

- use a bounded LRU of path / mtime-hash / read range / last access time;
- register file state after a successful read;
- the *real* mtime/hash check and write rejection are performed by the file
  tool / backend adapter — this middleware only maintains the model-facing
  state, the stale hint, and compaction-recovery references;
- when bash / an external tool modifies a tracked file, mark it stale and
  inject a short hint at the next model call;
- before compaction, record ``active_file_refs``; after compaction, restore a
  bounded excerpt of the most recent critical files;
- sub-agent isolated mode uses an independent cache; fork clones the allowed
  file state (no shared mutable container).

Self-containment: file fingerprints (mtime/hash) are obtained via an injected
``fingerprint_parser``; no ``runtime``/``service`` calls.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
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
from langchain_core.messages import SystemMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

if TYPE_CHECKING:
    from collections.abc import Awaitable

from deepagents.middleware._utils import append_to_system_message

logger = logging.getLogger(__name__)


READ_TOOL_NAMES = frozenset({"read_file", "read", "cat"})
WRITE_TOOL_NAMES = frozenset({"edit_file", "write_file", "write", "edit"})
SHELL_TOOL_NAMES = frozenset({"execute", "bash", "shell"})


@dataclass
class FileState:
    path: str
    mtime: str | None = None
    content_hash: str | None = None
    read_range: tuple[int, int] | None = None
    last_access: str | None = None
    stale: bool = False


@dataclass
class FileFingerprint:
    """A file fingerprint extracted from a read result by the injected parser."""

    path: str
    mtime: str | None = None
    content_hash: str | None = None
    read_range: tuple[int, int] | None = None


def _default_fingerprint_parser(request: ToolCallRequest, result: Any) -> FileFingerprint | None:
    """Extract a fingerprint from a read_file tool result, if present.

    The default looks for ``file_path`` in the tool args and a mtime/hash in
    the result's ``additional_kwargs`` (where backends stamp them). Callers
    inject a richer parser when the backend exposes more.
    """
    tool_name = str(request.tool_call.get("name") or "")
    if tool_name not in READ_TOOL_NAMES:
        return None
    args = request.tool_call.get("args") or {}
    path = args.get("file_path") or args.get("path")
    if not isinstance(path, str) or not path:
        return None
    mtime: str | None = None
    content_hash: str | None = None
    meta = dict(getattr(result, "additional_kwargs", {}) or {})
    if isinstance(meta.get("mtime"), str):
        mtime = meta["mtime"]
    if isinstance(meta.get("content_hash") or meta.get("hash"), str):
        content_hash = meta.get("content_hash") or meta.get("hash")
    offset = args.get("offset")
    limit = args.get("limit")
    read_range: tuple[int, int] | None = None
    if isinstance(offset, int) and isinstance(limit, int):
        read_range = (offset, offset + limit)
    return FileFingerprint(path=path, mtime=mtime, content_hash=content_hash, read_range=read_range)


def _paths_touched_by_write(request: ToolCallRequest) -> list[str]:
    args = request.tool_call.get("args") or {}
    paths: list[str] = []
    for key in ("file_path", "path"):
        val = args.get(key)
        if isinstance(val, str) and val:
            paths.append(val)
    return paths


def _paths_touched_by_shell(request: ToolCallRequest) -> list[str]:
    """Best-effort: shell tools can modify any tracked file → return [].

    The middleware marks *all* tracked files stale when a shell tool runs,
    because its output cannot reliably name modified paths. This is the
    conservative Claude-Code-style behaviour.
    """
    return []


STALE_HINT_TEMPLATE = """## File Context Notice

The following files you previously read may have changed since:
{stale_list}

Re-read a file before editing it to avoid acting on stale content."""


class FileContextMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Track read-file state and inject stale hints at the model boundary."""

    def __init__(
        self,
        *,
        max_files: int = 50,
        fingerprint_parser: Callable[[ToolCallRequest, Any], FileFingerprint | None] | None = None,
    ) -> None:
        self._max_files = max(1, max_files)
        self._parse_fingerprint = fingerprint_parser or _default_fingerprint_parser

    @staticmethod
    def _registry(state: AgentState[Any]) -> OrderedDict[str, FileState]:
        raw = state.get("_file_context") or {}
        out: OrderedDict[str, FileState] = OrderedDict()
        for path, entry in raw.items():
            if isinstance(entry, FileState):
                out[path] = entry
            elif isinstance(entry, dict):
                out[path] = FileState(**entry)
        return out

    @staticmethod
    def _commit(state: AgentState[Any], registry: OrderedDict[str, FileState]) -> None:
        state["_file_context"] = {p: s for p, s in registry.items()}  # type: ignore[assignment]

    def _register(self, registry: OrderedDict[str, FileState], fp: FileFingerprint) -> None:
        existing = registry.get(fp.path)
        last_access = existing.last_access if existing else None
        state = FileState(
            path=fp.path,
            mtime=fp.mtime,
            content_hash=fp.content_hash,
            read_range=fp.read_range,
            last_access=last_access,
            stale=False,
        )
        registry[fp.path] = state  # refreshes LRU order
        registry.move_to_end(fp.path)
        while len(registry) > self._max_files:
            registry.popitem(last=False)

    def _maybe_register(
        self,
        request: ToolCallRequest,
        result: Any,
        registry: OrderedDict[str, FileState],
    ) -> None:
        fp = self._parse_fingerprint(request, result)
        if fp is None:
            return
        self._register(registry, fp)

    def _mark_stale_for(self, request: ToolCallRequest, registry: OrderedDict[str, FileState]) -> None:
        tool_name = str(request.tool_call.get("name") or "")
        if tool_name in SHELL_TOOL_NAMES:
            for state in registry.values():
                state.stale = True
            return
        if tool_name in WRITE_TOOL_NAMES:
            for path in _paths_touched_by_write(request):
                if path in registry:
                    registry[path].stale = True
                    registry[path].last_access = None

    def _stale_hint(self, registry: OrderedDict[str, FileState]) -> str | None:
        stale_paths = [p for p, s in registry.items() if s.stale]
        if not stale_paths:
            return None
        return STALE_HINT_TEMPLATE.format(stale_list="\n".join(f"- {p}" for p in stale_paths))

    def active_file_refs(self, state: AgentState[Any], *, limit: int = 10) -> list[str]:
        """Return the most-recently-accessed file paths (for compaction recovery).

        Uses LRU order (most-recently-used last in the OrderedDict) so the result
        is deterministic without a wall clock.
        """
        registry = self._registry(state)
        ordered = list(reversed(registry.keys()))  # MRU first
        return ordered[:limit]

    # -- tool-call seam: register reads, mark writes/bash stale -------------

    def _after_tool(self, request: ToolCallRequest, result: Any, state: AgentState[Any]) -> None:
        registry = self._registry(state)
        before_keys = set(registry.keys())
        before_stale = any(s.stale for s in registry.values())

        fp = self._parse_fingerprint(request, result)
        if fp is not None:
            self._register(registry, fp)
        self._mark_stale_for(request, registry)

        dirty = (
            set(registry.keys()) != before_keys
            or any(s.stale for s in registry.values()) != before_stale
            or fp is not None
        )
        if dirty:
            self._commit(state, registry)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        result = handler(request)
        self._after_tool(request, result, request.state)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        result = await handler(request)
        self._after_tool(request, result, request.state)
        return result

    # -- model-call seam: inject stale hint -------------------------------

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        registry = self._registry(request.state)
        hint = self._stale_hint(registry)
        if hint is None:
            return request
        return request.override(system_message=append_to_system_message(request.system_message, hint))

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
    "FileContextMiddleware",
    "FileFingerprint",
    "FileState",
    "READ_TOOL_NAMES",
    "SHELL_TOOL_NAMES",
    "WRITE_TOOL_NAMES",
]
