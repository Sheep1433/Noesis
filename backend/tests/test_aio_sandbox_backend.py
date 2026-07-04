"""AioSandboxBackend：mock agent_sandbox SDK 行为。"""

from __future__ import annotations

import base64
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.backends.aio_sandbox import AioSandboxBackend


class _FakeShell:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._lock = threading.Lock()

    def exec_command(self, *, command: str, exec_dir: str | None = None, timeout: float | None = None):
        with self._lock:
            self.calls.append((command, exec_dir))
        return SimpleNamespace(
            data=SimpleNamespace(output="ok\n", exit_code=0, truncated=False),
        )


class _FakeFile:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write_file(self, *, file: str, content: str, encoding: str | None = None) -> None:
        if encoding == "base64":
            self.files[file] = base64.b64decode(content)
        else:
            self.files[file] = content.encode("utf-8")

    def read_file(self, *, file: str):
        if file not in self.files:
            raise FileNotFoundError(file)
        return SimpleNamespace(data=SimpleNamespace(content=self.files[file].decode("utf-8")))


@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.shell = _FakeShell()
    client.file = _FakeFile()
    return client


@pytest.fixture
def backend(fake_client: MagicMock) -> AioSandboxBackend:
    return AioSandboxBackend(
        base_url="http://aio:8080",
        user_id="u1",
        session_id="s1",
        client=fake_client,
    )


def test_execute_uses_session_workspace_exec_dir(backend: AioSandboxBackend) -> None:
    backend.execute("echo hi")
    assert backend._client.shell.calls[0][1] == "/workspace/sessions/s1/workspace"


def test_upload_uses_absolute_workspace_path(backend: AioSandboxBackend) -> None:
    path = "/workspace/sessions/s1/workspace/notes.md"
    result = backend.upload_files([(path, b"hello")])
    assert result[0].error is None
    assert path in backend._client.file.files


def test_upload_writes_utf8_text_not_base64_literal(backend: AioSandboxBackend) -> None:
    path = "/workspace/sessions/s1/workspace/research/plan.md"
    markdown = "# 研究规划\n\n中文正文"
    result = backend.upload_files([(path, markdown.encode("utf-8"))])
    assert result[0].error is None
    stored = backend._client.file.files[path]
    assert stored == markdown.encode("utf-8")
    assert not stored.decode("ascii", errors="ignore").startswith("IyD")
