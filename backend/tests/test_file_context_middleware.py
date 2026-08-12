"""Unit contracts for ``FileContextMiddleware`` (read state + stale hints)."""

from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from noesis.middleware.file_context_middleware import (
    FileContextMiddleware,
    FileFingerprint,
    FileState,
)


def _read_call(path: str, state, call_id: str = "r1", offset=None, limit=None) -> ToolCallRequest:
    args = {"file_path": path}
    if offset is not None:
        args["offset"] = offset
    if limit is not None:
        args["limit"] = limit
    return ToolCallRequest(
        tool_call={"name": "read_file", "args": args, "id": call_id},
        tool=None,
        state=state,
        runtime=None,
    )


def _write_call(path: str, state, call_id: str = "w1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "edit_file", "args": {"file_path": path, "content": "x"}, "id": call_id},
        tool=None,
        state=state,
        runtime=None,
    )


def _shell_call(state, call_id: str = "s1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "execute", "args": {"command": "sed -i ..."}, "id": call_id},
        tool=None,
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


def _model_request(state) -> ModelRequest:
    return ModelRequest(model=object(), messages=[], system_message=SystemMessage(content="sys"), state=state)  # type: ignore[arg-type]


def test_successful_read_registers_file_state() -> None:
    mw = FileContextMiddleware()
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/a.txt", state), _handler_returning(_read_result(mtime="1", content_hash="h1")))
    registry = FileContextMiddleware._registry(state)
    assert "/a.txt" in registry
    assert registry["/a.txt"].mtime == "1"
    assert registry["/a.txt"].content_hash == "h1"
    assert registry["/a.txt"].stale is False


def test_lru_evicts_beyond_capacity() -> None:
    mw = FileContextMiddleware(max_files=2)
    state = {"messages": []}
    for path in ["/a", "/b", "/c"]:
        mw.wrap_tool_call(_read_call(path, state, call_id=path), _handler_returning(_read_result(mtime="t")))
    registry = FileContextMiddleware._registry(state)
    assert set(registry.keys()) == {"/b", "/c"}


def test_edit_marks_read_file_stale() -> None:
    mw = FileContextMiddleware()
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/a.txt", state), _handler_returning(_read_result(mtime="1")))
    assert FileContextMiddleware._registry(state)["/a.txt"].stale is False

    mw.wrap_tool_call(_write_call("/a.txt", state), _handler_returning(ToolMessage(content="ok", tool_call_id="w1", name="edit_file")))
    assert FileContextMiddleware._registry(state)["/a.txt"].stale is True


def test_shell_marks_all_tracked_files_stale() -> None:
    mw = FileContextMiddleware()
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/a", state), _handler_returning(_read_result(mtime="1")))
    mw.wrap_tool_call(_read_call("/b", state, call_id="rb"), _handler_returning(_read_result(mtime="1")))
    mw.wrap_tool_call(_shell_call(state), _handler_returning(ToolMessage(content="done", tool_call_id="s1", name="execute")))
    registry = FileContextMiddleware._registry(state)
    assert registry["/a"].stale is True
    assert registry["/b"].stale is True


def test_model_request_injects_stale_hint_only_when_stale() -> None:
    mw = FileContextMiddleware()
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/a.txt", state), _handler_returning(_read_result(mtime="1")))
    # no stale yet → no hint
    modified = mw.modify_request(_model_request(state))
    assert modified.system_message.text == "sys"

    mw.wrap_tool_call(_write_call("/a.txt", state), _handler_returning(ToolMessage(content="ok", tool_call_id="w1", name="edit_file")))
    modified = mw.modify_request(_model_request(state))
    assert "File Context Notice" in modified.system_message.text
    assert "/a.txt" in modified.system_message.text


def test_active_file_refs_returns_recent_paths() -> None:
    mw = FileContextMiddleware()
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/old", state), _handler_returning(_read_result(mtime="1")))
    mw.wrap_tool_call(_read_call("/new", state, call_id="rn"), _handler_returning(_read_result(mtime="2")))
    refs = mw.active_file_refs(state, limit=5)
    assert refs[0] == "/new"
    assert "/old" in refs


def test_custom_fingerprint_parser_injection() -> None:
    calls = []

    def parser(request, result):  # noqa: ANN001
        calls.append(request.tool_call.get("file_path"))
        return FileFingerprint(path="/custom", mtime="z")

    mw = FileContextMiddleware(fingerprint_parser=parser)
    state = {"messages": []}
    mw.wrap_tool_call(_read_call("/actual.txt", state), _handler_returning(_read_result()))
    registry = FileContextMiddleware._registry(state)
    assert "/custom" in registry
    assert registry["/custom"].mtime == "z"


def test_non_read_tool_does_not_register() -> None:
    mw = FileContextMiddleware()
    state = {"messages": []}
    mw.wrap_tool_call(_shell_call(state), _handler_returning(ToolMessage(content="x", tool_call_id="s1", name="execute")))
    assert FileContextMiddleware._registry(state) == {}
