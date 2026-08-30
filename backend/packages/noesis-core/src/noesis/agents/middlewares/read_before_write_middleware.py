"""Read-before-write middleware with a hash-version gate.

The middleware records the content hash returned by a successful file read.
Before a write/edit handler runs it verifies that the file still has that
hash, then atomically consumes the recorded version.  Consuming the version
before calling the handler makes two writes based on one read impossible.

The implementation intentionally owns no prompt injection, LRU, stale hints,
excerpts, or compaction recovery state.
"""

from __future__ import annotations

import hashlib
import inspect
import asyncio
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Callable, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    PrivateStateAttr,
    ResponseT,
)
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langchain_core.messages import ToolMessage

from deepagents.backends import BackendProtocol

from noesis.errors.tool_failure import ToolValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable


READ_TOOL_NAMES = frozenset({"read_file", "read", "cat"})
WRITE_TOOL_NAMES = frozenset({"edit_file", "write_file", "write", "edit"})
_VERSIONS_KEY = "_read_before_write_versions"

# State keys this middleware owns; subagent isolation must carry these over.
PRIVATE_STATE_KEYS: tuple[str, ...] = (_VERSIONS_KEY,)


def _merge_versions(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """合并 read-before-write 版本记录。

    不同路径的 hash 互不冲突，直接 union；同路径取后者覆盖。这让同一
    super-step 内多个文件工具返回的 Command 能合并该 key，而非触发
    LangGraph ``InvalidUpdateError``。
    """
    merged: dict[str, str] = dict(left or {})
    merged.update(right or {})
    return merged


# 写入调用缺 file_path：多为模型输出被 provider 截断（finish_reason=length）
# 时 JSON 后段参数丢失——错误文案给出原因与出路，模型可在下一轮分片重写
_MISSING_PATH_MESSAGE = (
    "写入缺少 file_path：模型输出可能被截断（finish_reason=length）。"
    "请缩短单次写入内容、分片重试"
)


class WriteRejectedError(ToolValidationError):
    """写前校验拒绝（未读先写 / 版本冲突 / 参数缺失）。

    继承 ToolValidationError：归类 invalid_arguments，用户文案自动
    携带具体原因（见 tool_failure._user_message_with_detail）。
    """


@dataclass(frozen=True)
class FileFingerprint:
    """The path and version observed by a successful read."""

    path: str
    mtime: str | None = None
    content_hash: str | None = None
    read_range: tuple[int, int] | None = None


class ReadBeforeWriteState(AgentState[ResponseT]):
    _read_before_write_versions: NotRequired[
        Annotated[dict[str, str], PrivateStateAttr, _merge_versions]
    ]


def _path(request: ToolCallRequest) -> str | None:
    args = request.tool_call.get("args") or {}
    value = args.get("file_path") or args.get("path")
    return value if isinstance(value, str) and value else None


def _default_fingerprint_parser(request: ToolCallRequest, result: Any) -> FileFingerprint | None:
    if str(request.tool_call.get("name") or "") not in READ_TOOL_NAMES:
        return None
    path = _path(request)
    if path is None or getattr(result, "status", None) == "error":
        return None
    metadata = dict(getattr(result, "additional_kwargs", {}) or {})
    content_hash = metadata.get("content_hash") or metadata.get("hash")
    if not isinstance(content_hash, str) or not content_hash:
        content_hash = None
    mtime = metadata.get("mtime") if isinstance(metadata.get("mtime"), str) else None
    args = request.tool_call.get("args") or {}
    offset = args.get("offset")
    limit = args.get("limit")
    read_range = (offset, offset + limit) if isinstance(offset, int) and isinstance(limit, int) else None
    return FileFingerprint(path=path, mtime=mtime, content_hash=content_hash, read_range=read_range)


def _successful(result: Any) -> bool:
    return getattr(result, "status", None) != "error"


def _hash_read_result(result: Any, path: str) -> str | None:
    error = getattr(result, "error", None)
    if error:
        err_text = str(error).casefold()
        if "file_not_found" in err_text or "path_not_found" in err_text:
            return None
        raise WriteRejectedError(f"cannot verify current version of {path}: {error}")
    if isinstance(result, str):
        content = result
    else:
        file_data = getattr(result, "file_data", None)
        if not isinstance(file_data, dict) or "content" not in file_data:
            raise WriteRejectedError(f"cannot verify current version of {path}")
        content = file_data["content"]
    if not isinstance(content, str):
        content = repr(content)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ReadBeforeWriteMiddleware(
    AgentMiddleware[ReadBeforeWriteState[ResponseT], ContextT, ResponseT]
):
    """Reject writes unless they consume a still-current read hash."""

    state_schema = ReadBeforeWriteState

    def __init__(
        self,
        *,
        backend: BackendProtocol | None = None,
        current_hash: Callable[[str], str | Awaitable[str]] | None = None,
        fingerprint_parser: Callable[[ToolCallRequest, Any], FileFingerprint | None] | None = None,
    ) -> None:
        self._backend = backend
        self._current_hash = current_hash
        self._parse_fingerprint = fingerprint_parser or _default_fingerprint_parser
        self._gate = threading.RLock()
        self._async_gate = asyncio.Lock()

    @staticmethod
    def _versions(state: AgentState[Any]) -> dict[str, str]:
        return dict(state.get(_VERSIONS_KEY) or {})  # type: ignore[arg-type]

    @staticmethod
    def _commit(state: AgentState[Any], versions: dict[str, str]) -> None:
        state[_VERSIONS_KEY] = versions  # type: ignore[assignment]

    def _record_read(
        self,
        request: ToolCallRequest,
        result: Any,
        stable_hash: str | None = None,
    ) -> FileFingerprint | None:
        fingerprint = self._parse_fingerprint(request, result)
        if fingerprint is None:
            return None
        content_hash = fingerprint.content_hash or stable_hash
        if not content_hash:
            return None
        with self._gate:
            versions = self._versions(request.state)
            versions[fingerprint.path] = content_hash
            self._commit(request.state, versions)
        return FileFingerprint(
            path=fingerprint.path,
            mtime=fingerprint.mtime,
            content_hash=content_hash,
            read_range=fingerprint.read_range,
        )

    @staticmethod
    def _with_read_mark(result: Any, fingerprint: FileFingerprint | None) -> Any:
        if fingerprint is None or not isinstance(result, ToolMessage):
            return result
        metadata = dict(result.additional_kwargs)
        metadata["noesis_read_mark"] = {
            "path": fingerprint.path,
            "content_hash": fingerprint.content_hash,
            "mtime": fingerprint.mtime,
            "read_range": fingerprint.read_range,
        }
        return result.model_copy(update={"additional_kwargs": metadata})

    def _observed_hash(self, request: ToolCallRequest, path: str) -> str | None:
        if self._current_hash is not None:
            observed = self._current_hash(path)
            if inspect.isawaitable(observed):
                raise WriteRejectedError("async hash verifier cannot be used by wrap_tool_call")
            return observed
        if self._backend is None:
            raise WriteRejectedError(f"read before write hash verifier unavailable for {path}")
        return _hash_read_result(
            self._backend.read(path, offset=0, limit=1_000_000_000),
            path,
        )

    async def _aobserved_hash(self, request: ToolCallRequest, path: str) -> str | None:
        if self._current_hash is not None:
            observed = self._current_hash(path)
            return await observed if inspect.isawaitable(observed) else observed
        if self._backend is None:
            raise WriteRejectedError(f"read before write hash verifier unavailable for {path}")
        return _hash_read_result(
            await self._backend.aread(path, offset=0, limit=1_000_000_000),
            path,
        )

    def _claim_write(
        self,
        request: ToolCallRequest,
        observed_hash: str | None,
    ) -> tuple[str, str | None]:
        path = _path(request)
        if path is None:
            raise WriteRejectedError("write request has no file path")
        with self._gate:
            versions = self._versions(request.state)
            read_hash = versions.get(path)
            tool_name = str(request.tool_call.get("name") or "")
            if observed_hash is None and tool_name in {"write", "write_file"}:
                return path, None
            if read_hash is None:
                raise WriteRejectedError(f"read before write required for {path}")
            if observed_hash != read_hash:
                raise WriteRejectedError(f"{path} changed since read")
            del versions[path]
            self._commit(request.state, versions)
        return path, read_hash

    def _restore_claim(
        self,
        state: AgentState[Any],
        path: str,
        read_hash: str | None,
    ) -> None:
        if read_hash is None:
            return
        with self._gate:
            versions = self._versions(state)
            versions.setdefault(path, read_hash)
            self._commit(state, versions)

    @classmethod
    def _with_versions(cls, result: Any, state: AgentState[Any]) -> Any:
        update = {_VERSIONS_KEY: cls._versions(state)}
        if isinstance(result, Command):
            if not isinstance(result.update, dict):
                return result
            return Command(
                graph=result.graph,
                update={**result.update, **update},
                resume=result.resume,
                goto=result.goto,
            )
        return Command(update={"messages": [result], **update})

    def _run_sync(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        tool_name = str(request.tool_call.get("name") or "")
        claim: tuple[str, str | None] | None = None
        read_hash_before: str | None = None
        if tool_name in READ_TOOL_NAMES:
            path = _path(request)
            if path is not None:
                try:
                    read_hash_before = self._observed_hash(request, path)
                except WriteRejectedError:
                    pass
        if tool_name in WRITE_TOOL_NAMES:
            path = _path(request)
            if path is None:
                raise WriteRejectedError(_MISSING_PATH_MESSAGE)
            claim = self._claim_write(request, self._observed_hash(request, path))
        try:
            result = handler(request)
        except BaseException:
            if claim is not None:
                self._restore_claim(request.state, *claim)
            raise
        if claim is not None and not _successful(result):
            self._restore_claim(request.state, *claim)
        stable_hash: str | None = None
        if read_hash_before is not None:
            path = _path(request)
            try:
                read_hash_after = self._observed_hash(request, path) if path is not None else None
            except WriteRejectedError:
                read_hash_after = None
            if read_hash_after == read_hash_before:
                stable_hash = read_hash_after
        fingerprint = self._record_read(request, result, stable_hash)
        result = self._with_read_mark(result, fingerprint)
        if claim is not None or fingerprint is not None:
            return self._with_versions(result, request.state)
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        tool_name = str(request.tool_call.get("name") or "")
        if tool_name not in READ_TOOL_NAMES | WRITE_TOOL_NAMES:
            return handler(request)
        with self._gate:
            return self._run_sync(request, handler)

    async def _run_async(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        tool_name = str(request.tool_call.get("name") or "")
        claim: tuple[str, str | None] | None = None
        read_hash_before: str | None = None
        if tool_name in READ_TOOL_NAMES:
            path = _path(request)
            if path is not None:
                try:
                    read_hash_before = await self._aobserved_hash(request, path)
                except WriteRejectedError:
                    pass
        if tool_name in WRITE_TOOL_NAMES:
            path = _path(request)
            if path is None:
                raise WriteRejectedError(_MISSING_PATH_MESSAGE)
            claim = self._claim_write(request, await self._aobserved_hash(request, path))
        try:
            result = await handler(request)
        except BaseException:
            if claim is not None:
                self._restore_claim(request.state, *claim)
            raise
        if claim is not None and not _successful(result):
            self._restore_claim(request.state, *claim)
        stable_hash: str | None = None
        if read_hash_before is not None:
            path = _path(request)
            try:
                read_hash_after = await self._aobserved_hash(request, path) if path is not None else None
            except WriteRejectedError:
                read_hash_after = None
            if read_hash_after == read_hash_before:
                stable_hash = read_hash_after
        fingerprint = self._record_read(request, result, stable_hash)
        result = self._with_read_mark(result, fingerprint)
        if claim is not None or fingerprint is not None:
            return self._with_versions(result, request.state)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        tool_name = str(request.tool_call.get("name") or "")
        if tool_name not in READ_TOOL_NAMES | WRITE_TOOL_NAMES:
            return await handler(request)
        async with self._async_gate:
            return await self._run_async(request, handler)


__all__ = [
    "FileFingerprint",
    "PRIVATE_STATE_KEYS",
    "READ_TOOL_NAMES",
    "ReadBeforeWriteMiddleware",
    "ReadBeforeWriteState",
    "WRITE_TOOL_NAMES",
    "WriteRejectedError",
]
