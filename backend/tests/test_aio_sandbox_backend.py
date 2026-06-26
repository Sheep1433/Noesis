"""AioSandboxBackend：mock agent_sandbox、mutex 与绝对路径。"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.backends.aio_sandbox import AioSandboxBackend, _session_mutex, create_aio_agent_backend


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

    def write_file(self, *, file: str, content: bytes) -> None:
        self.files[file] = content

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


def test_resolve_read_requires_workspace_absolute_path(backend: AioSandboxBackend) -> None:
    path = "/workspace/sessions/s1/workspace/research/report.md"
    assert backend._resolve_read_path(path) == path


def test_resolve_read_skills_under_workspace(backend: AioSandboxBackend) -> None:
    path = "/workspace/skills/deep-research-v2/SKILL.md"
    assert backend._resolve_read_path(path) == path


def test_resolve_read_rejects_non_absolute(backend: AioSandboxBackend) -> None:
    with pytest.raises(ValueError, match="absolute"):
        backend._resolve_read_path("research/report.md")


def test_resolve_write_allows_any_workspace_path(backend: AioSandboxBackend) -> None:
    path = "/workspace/sessions/s2/workspace/report.md"
    assert backend._resolve_write_path(path) == path


@patch("agent.backends.aio_sandbox.is_platform_skill_entry", return_value=True)
def test_resolve_write_blocks_platform_skill_symlink(
    _mock: MagicMock, backend: AioSandboxBackend
) -> None:
    with pytest.raises(ValueError, match="read-only"):
        backend._resolve_write_path("/workspace/skills/deep-research-v2/SKILL.md")


def test_resolve_read_blocks_traversal(backend: AioSandboxBackend) -> None:
    with pytest.raises(ValueError, match="traversal"):
        backend._resolve_read_path("/workspace/../etc/passwd")


def test_execute_uses_workspace_exec_dir(backend: AioSandboxBackend) -> None:
    backend.execute("echo hi")
    assert backend._client.shell.calls[0][1] == "/workspace"


def test_upload_uses_absolute_workspace_path(backend: AioSandboxBackend) -> None:
    path = "/workspace/sessions/s1/workspace/notes.md"
    result = backend.upload_files([(path, b"hello")])
    assert result[0].error is None
    assert path in backend._client.file.files


def test_different_sessions_may_use_parallel_mutex_keys() -> None:
    assert _session_mutex("u1", "s1") is not _session_mutex("u1", "s2")


@pytest.mark.asyncio
async def test_create_aio_agent_backend_returns_single_backend() -> None:
    with patch(
        "services.sandbox_service.ensure_user_sandbox",
        new_callable=AsyncMock,
        return_value="http://aio:8080",
    ):
        backend = await create_aio_agent_backend("u1", "s1")
    assert isinstance(backend, AioSandboxBackend)
