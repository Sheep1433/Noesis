"""summary_offload 与 FilesystemMiddleware 共用 backend 的回归测试。"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        # 卸载文件落在 backend 根下 summary_offload/
        assert (Path(root) / "summary_offload").is_dir()


def test_offload_under_session_workspace_backend() -> None:
    """会话 backend 根为 workspace/ 时，卸载落在 workspace/summary_offload/。"""
    from config import agent_workspace_paths as paths

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "agent_workspace"
        with patch.object(paths, "_resolve_root", return_value=root):
            workspace = paths.ensure_workspace_dir("u1", "s1")
            backend = create_local_shell_backend(workspace, virtual_mode=True)
            msg = ToolMessage(
                content="y" * 8000,
                tool_call_id="call-offload-2",
                name="web_fetch",
                id="a2050eab-acff-4d32-8a80-e4145a0448a0",
            )
            token_counter = lambda messages: 5000  # noqa: ARG005

            _process_tool_message(
                msg,
                threshold=1000,
                token_counter=token_counter,
                backend=backend,
            )

            offload_dir = workspace / "summary_offload"
            assert offload_dir.is_dir()
            assert any(offload_dir.iterdir())
