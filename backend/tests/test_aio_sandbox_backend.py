"""AioSandboxBackend：mock agent_sandbox、mutex 与 virtual 路径。"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.backends.aio_sandbox import AioSandboxBackend, _session_mutex


class _FakeShell:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def exec_command(self, *, command: str, exec_dir: str | None = None, timeout: float | None = None):
        with self._lock:
            self.calls.append(command)
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


def test_resolve_path_maps_virtual_root(fake_client: MagicMock) -> None:
    backend = AioSandboxBackend(
        base_url="http://aio:8080",
        user_id="u1",
        session_id="s1",
        root_dir="/workspace/sessions/s1/workspace",
        client=fake_client,
    )
    assert backend._resolve_path("/notes.md") == (
        "/workspace/sessions/s1/workspace/notes.md"
    )


def test_resolve_path_blocks_traversal(fake_client: MagicMock) -> None:
    backend = AioSandboxBackend(
        base_url="http://aio:8080",
        user_id="u1",
        session_id="s1",
        root_dir="/workspace/sessions/s1/workspace",
        client=fake_client,
    )
    with pytest.raises(ValueError, match="traversal"):
        backend._resolve_path("/../etc/passwd")


def test_execute_serializes_same_session(fake_client: MagicMock) -> None:
    backend = AioSandboxBackend(
        base_url="http://aio:8080",
        user_id="u1",
        session_id="s1",
        root_dir="/workspace/sessions/s1/workspace",
        client=fake_client,
    )
    order: list[int] = []
    barrier = threading.Barrier(2, timeout=2)

    def run_one(tag: int) -> None:
        barrier.wait()
        backend.execute(f"echo {tag}")
        order.append(tag)

    t1 = threading.Thread(target=run_one, args=(1,))
    t2 = threading.Thread(target=run_one, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(fake_client.shell.calls) == 2
    assert len(order) == 2


def test_different_sessions_may_use_parallel_mutex_keys() -> None:
    assert _session_mutex("u1", "s1") is not _session_mutex("u1", "s2")


def test_upload_uses_container_absolute_path(fake_client: MagicMock) -> None:
    backend = AioSandboxBackend(
        base_url="http://aio:8080",
        user_id="u1",
        session_id="s1",
        root_dir="/workspace/sessions/s1/workspace",
        client=fake_client,
    )
    result = backend.upload_files([("/notes.md", b"hello")])
    assert result[0].error is None
    assert "/workspace/sessions/s1/workspace/notes.md" in fake_client.file.files


def test_execute_injects_browser_env(fake_client: MagicMock) -> None:
    backend = AioSandboxBackend(
        base_url="http://aio:8080",
        user_id="u1",
        session_id="s1",
        root_dir="/workspace/sessions/s1/workspace",
        inject_browser_env=True,
        client=fake_client,
    )
    backend.execute("echo hi")
    cmd = fake_client.shell.calls[0]
    assert "SANDBOX_HEADLESS=1" in cmd
    assert "BAOYU_CHROME_PROFILE_DIR=/workspace/sessions/s1/workspace/.chrome-profile" in cmd
    assert "SANDBOX_CDP_PORT=" in cmd


@patch("services.sandbox_service._docker_available", return_value=False)
@pytest.mark.asyncio
async def test_ensure_user_sandbox_fails_without_docker(_mock: MagicMock) -> None:
    from domain.chat.streaming.tool_errors import ToolInfrastructureError
    from services import sandbox_service

    sandbox_service._BASE_URL_CACHE.clear()
    with pytest.raises(ToolInfrastructureError, match="Docker"):
        await sandbox_service.ensure_user_sandbox("u1")


@pytest.mark.asyncio
async def test_create_user_sandbox_backend_has_user_skills_route() -> None:
    from unittest.mock import AsyncMock

    from agent.backends.aio_sandbox import create_user_sandbox_backend

    with patch(
        "services.sandbox_service.ensure_user_sandbox",
        new_callable=AsyncMock,
        return_value="http://aio:8080",
    ):
        backend = await create_user_sandbox_backend("u1", "s1")
    assert "/skills/" in backend.routes
    assert "/user-skills/" in backend.routes
