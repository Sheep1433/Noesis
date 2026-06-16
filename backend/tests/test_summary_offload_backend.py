"""summary_offload 与 FilesystemMiddleware 共用 backend 的回归测试。"""
from __future__ import annotations

import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.messages import ToolMessage

from agent.backends.local_shell import create_local_shell_backend
from agent.middlewares.summary_offload_middleware import (
    _build_offload_file_path,
    _process_tool_message,
    _resolve_filesystem_backend,
)


def test_resolve_filesystem_backend_prefers_injected_over_state() -> None:
    disk_backend = MagicMock(name="disk_backend")
    runtime = MagicMock(context=None)
    state = {"messages": [], "files": {}}

    resolved = _resolve_filesystem_backend(runtime, state, injected=disk_backend)

    assert resolved is disk_backend


def test_offload_writes_to_injected_disk_backend_and_is_readable() -> None:
    with tempfile.TemporaryDirectory() as root:
        backend = create_local_shell_backend(root, virtual_mode=True)
        msg = ToolMessage(
            content="x" * 8000,
            tool_call_id="call-offload-1",
            name="web_search",
            id="92050eab-acff-4d32-8a80-e4145a0448a9",
        )
        token_counter = lambda messages: 5000  # noqa: ARG005

        result = _process_tool_message(
            msg,
            threshold=1000,
            token_counter=token_counter,
            backend=backend,
        )

        assert result is not None
        file_path = _build_offload_file_path(msg)
        read_result = backend.read(file_path)
        assert read_result.error is None
        assert read_result.file_data is not None
        assert "web_search" in read_result.file_data.get("content", "")
        assert "[ToolResultOffloaded]" in msg.content
        assert file_path in msg.content
