"""Unit contracts for the read-before-write hash gate."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, NotRequired, TypedDict

import pytest
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from noesis.agents.middlewares.read_before_write_middleware import (
    ReadBeforeWriteMiddleware,
    FileFingerprint,
    WriteRejectedError,
    _merge_versions,
)
from noesis.errors.tool_failure import (
    ToolFailureCategory,
    classify_tool_failure,
)


def _read_call(path: str, state, call_id: str = "r1", offset=None, limit=None, tool=None) -> ToolCallRequest:
    args = {"file_path": path}
    if offset is not None:
        args["offset"] = offset
    if limit is not None:
        args["limit"] = limit
    return ToolCallRequest(
        tool_call={"name": "read_file", "args": args, "id": call_id},
        tool=tool,
        state=state,
        runtime=None,
    )


def _write_call(path: str, state, call_id: str = "w1", tool=None) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "edit_file", "args": {"file_path": path, "content": "x"}, "id": call_id},
        tool=tool,
        state=state,
        runtime=None,
    )


def _read_result(mtime: str | None = None, content_hash: str | None = None) -> ToolMessage:
    kwargs = {}
    if mtime:
        kwargs["mtime"] = mtime
    if content_hash:
        kwargs["content_hash"] = content_hash
    return ToolMessage(content="file body", tool_call_id="r1", name="read_file", additional_kwargs=kwargs)


def _handler_returning(result):
    def handler(_request):  # noqa: ANN001
        return result
    return handler


@dataclass
class _BackendReadResult:
    content: str
    error: str | None = None

    @property
    def file_data(self):
        return {"content": self.content, "encoding": "utf-8"}


class _Backend:
    def __init__(self) -> None:
        self.content = "v1"

    def read(self, _path, **_kwargs):  # noqa: ANN001
        return _BackendReadResult(self.content)

    async def aread(self, _path, **_kwargs):  # noqa: ANN001
        return _BackendReadResult(self.content)


class _MissingBackend(_Backend):
    def read(self, _path, **_kwargs):  # noqa: ANN001
        return _BackendReadResult("", error="file_not_found")

    async def aread(self, _path, **_kwargs):  # noqa: ANN001
        return _BackendReadResult("", error="file_not_found")


def test_successful_read_registers_hash_version() -> None:
    mw = ReadBeforeWriteMiddleware(current_hash=lambda _path: "h1")
    state = {"messages": []}
    result = mw.wrap_tool_call(
        _read_call("/a.txt", state),
        _handler_returning(_read_result(mtime="1", content_hash="h1")),
    )
    assert state["_read_before_write_versions"]["/a.txt"] == "h1"
    assert isinstance(result, Command)
    assert result.update["messages"][0].additional_kwargs["noesis_read_mark"] == {
        "path": "/a.txt",
        "content_hash": "h1",
        "mtime": "1",
        "read_range": None,
    }


def test_custom_fingerprint_parser_injection() -> None:
    calls = []

    def parser(request, result):  # noqa: ANN001
        calls.append(request.tool_call.get("file_path"))
        return FileFingerprint(path="/custom", content_hash="z")

    mw = ReadBeforeWriteMiddleware(current_hash=lambda _path: "z", fingerprint_parser=parser)
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/actual.txt", state), _handler_returning(_read_result()))
    assert state["_read_before_write_versions"]["/custom"] == "z"


def test_write_without_prior_read_is_rejected_before_handler() -> None:
    mw = ReadBeforeWriteMiddleware(current_hash=lambda _path: "h1")
    state = {"messages": []}
    called = False

    def handler(_request):  # noqa: ANN001
        nonlocal called
        called = True
        return ToolMessage(content="ok", tool_call_id="w1", name="edit_file")

    with pytest.raises(WriteRejectedError, match="read before write"):
        mw.wrap_tool_call(_write_call("/a.txt", state), handler)
    assert called is False


def test_write_rejects_when_current_hash_differs_from_read_version() -> None:
    hashes = {"/a.txt": "h1"}
    mw = ReadBeforeWriteMiddleware(current_hash=hashes.__getitem__)
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/a.txt", state), _handler_returning(_read_result(content_hash="h1")))
    hashes["/a.txt"] = "h2"

    with pytest.raises(WriteRejectedError, match="changed since read"):
        mw.wrap_tool_call(_write_call("/a.txt", state), _handler_returning("must not run"))


def test_matching_hash_allows_exactly_one_concurrent_write() -> None:
    mw = ReadBeforeWriteMiddleware(current_hash=lambda _path: "h1")
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/a.txt", state), _handler_returning(_read_result(content_hash="h1")))

    def write():
        return mw.wrap_tool_call(
            _write_call("/a.txt", state),
            lambda request: ToolMessage(
                content="written",
                tool_call_id=request.tool_call["id"],
                name="edit_file",
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write) for _ in range(2)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append("written" if isinstance(future.result(), Command) else "unexpected")
        except WriteRejectedError:
            outcomes.append("rejected")
    assert sorted(outcomes) == ["rejected", "written"]


@pytest.mark.asyncio
async def test_async_wrap_enforces_same_gate() -> None:
    mw = ReadBeforeWriteMiddleware(current_hash=lambda _path: "h1")
    state = {"messages": []}

    async def read_handler(_request):  # noqa: ANN001
        return _read_result(content_hash="h1")

    async def write_handler(request):  # noqa: ANN001
        await asyncio.sleep(0)
        return ToolMessage(
            content="written",
            tool_call_id=request.tool_call["id"],
            name="edit_file",
        )

    await mw.awrap_tool_call(_read_call("/a.txt", state), read_handler)
    results = await asyncio.gather(
        mw.awrap_tool_call(_write_call("/a.txt", state, "w1"), write_handler),
        mw.awrap_tool_call(_write_call("/a.txt", state, "w2"), write_handler),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Command) for result in results) == 1
    assert sum(isinstance(result, WriteRejectedError) for result in results) == 1


def test_default_stack_mode_uses_explicit_backend() -> None:
    backend = _Backend()
    mw = ReadBeforeWriteMiddleware(backend=backend)
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/a.txt", state), _handler_returning(_read_result()))

    result = mw.wrap_tool_call(
        _write_call("/a.txt", state),
        _handler_returning(ToolMessage(content="written", tool_call_id="w1", name="edit_file")),
    )

    assert isinstance(result, Command)
    assert result.update["messages"][0].content == "written"
    assert result.update["_read_before_write_versions"] == {}


def test_default_stack_mode_does_not_record_a_file_that_changed_during_read() -> None:
    backend = _Backend()
    mw = ReadBeforeWriteMiddleware(backend=backend)
    state = {"messages": []}

    def changing_read(_request):  # noqa: ANN001
        backend.content = "v2"
        return _read_result()

    mw.wrap_tool_call(_read_call("/a.txt", state), changing_read)

    assert state.get("_read_before_write_versions", {}) == {}


def test_write_file_can_create_a_new_file_without_a_read_mark() -> None:
    mw = ReadBeforeWriteMiddleware(backend=_MissingBackend())
    state = {"messages": []}
    request = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "args": {"file_path": "/new.txt", "content": "new"},
            "id": "w-new",
        },
        tool=None,
        state=state,
        runtime=None,
    )

    result = mw.wrap_tool_call(
        request,
        _handler_returning(
            ToolMessage(content="created", tool_call_id="w-new", name="write_file")
        ),
    )

    assert isinstance(result, Command)
    assert result.update["_read_before_write_versions"] == {}


def test_merge_versions_unions_disjoint_paths() -> None:
    """reducer 合并不同路径的版本记录，而非在同 step 冲突。"""
    assert _merge_versions({"/a.txt": "h1"}, {"/b.txt": "h2"}) == {
        "/a.txt": "h1",
        "/b.txt": "h2",
    }
    # 同路径后者覆盖
    assert _merge_versions({"/a.txt": "h1"}, {"/a.txt": "h2"}) == {"/a.txt": "h2"}
    # None / 空容错
    assert _merge_versions(None, {"/a.txt": "h1"}) == {"/a.txt": "h1"}  # type: ignore[arg-type]
    assert _merge_versions({"/a.txt": "h1"}, None) == {"/a.txt": "h1"}  # type: ignore[arg-type]


def test_parallel_file_tools_merge_versions_without_error() -> None:
    """同一 super-step 并行两个文件工具各自返回带 _VERSIONS_KEY 的 Command，
    有 reducer 合并，不抛 InvalidUpdateError。"""

    class S(TypedDict, total=False):
        messages: NotRequired[list]
        _read_before_write_versions: NotRequired[
            Annotated[dict[str, str], PrivateStateAttr, _merge_versions]
        ]

    def node_a(_state):
        return Command(update={"_read_before_write_versions": {"/a.txt": "hashA"}})

    def node_b(_state):
        return Command(update={"_read_before_write_versions": {"/b.txt": "hashB"}})

    graph = StateGraph(S)
    graph.add_node("a", node_a)
    graph.add_node("b", node_b)
    graph.add_edge(START, "a")
    graph.add_edge(START, "b")
    graph.add_edge("a", END)
    graph.add_edge("b", END)
    app = graph.compile()

    result = app.invoke({"messages": []})
    assert result["_read_before_write_versions"] == {
        "/a.txt": "hashA",
        "/b.txt": "hashB",
    }


class _PathPrefixMissingBackend(_Backend):
    """backend 返回带路径前缀的 file_not_found 错误（模拟生产实际格式）。"""

    def read(self, _path, **_kwargs):  # noqa: ANN001
        return _BackendReadResult("", error=f"File '{_path}': file_not_found")

    async def aread(self, _path, **_kwargs):  # noqa: ANN001
        return _BackendReadResult("", error=f"File '{_path}': file_not_found")


def test_file_not_found_with_path_prefix_allows_new_file() -> None:
    """带路径前缀的 file_not_found 应识别为'文件不存在'，允许 write_file 新建。"""
    mw = ReadBeforeWriteMiddleware(backend=_PathPrefixMissingBackend())
    state = {"messages": []}
    request = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "args": {"file_path": "/new.txt", "content": "new"},
            "id": "w-new",
        },
        tool=None,
        state=state,
        runtime=None,
    )

    result = mw.wrap_tool_call(
        request,
        _handler_returning(
            ToolMessage(content="created", tool_call_id="w-new", name="write_file")
        ),
    )

    assert isinstance(result, Command)
    assert result.update["_read_before_write_versions"] == {}


def test_write_without_file_path_reports_truncation_guidance() -> None:
    """缺 file_path 的写入调用给出可自纠的原因（多为模型输出被截断）。"""
    mw = ReadBeforeWriteMiddleware(current_hash=lambda _path: "h1")
    request = ToolCallRequest(
        tool_call={"name": "write_file", "args": {"content": "半篇报告…"}, "id": "w9"},
        tool=None,
        state={"messages": []},
        runtime=None,
    )

    with pytest.raises(WriteRejectedError, match="被截断") as exc_info:
        mw.wrap_tool_call(request, _handler_returning("must not run"))

    # 类型化错误：归类 invalid_arguments，用户文案携带具体原因
    failure = classify_tool_failure(exc_info.value, tool_name="write_file")
    assert failure.category == ToolFailureCategory.INVALID_ARGUMENTS
    assert "file_path" in failure.text
