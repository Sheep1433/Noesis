"""sandbox.backend 工厂：local_shell 与 aio 统一 create_agent_backend。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.backends import create_agent_backend, uses_aio_sandbox
from config.agent_workspace_paths import ensure_workspace_dir


@pytest.fixture
def local_shell_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_BACKEND", "local_shell")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()


@pytest.fixture
def aio_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_BACKEND", "aio")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()


def test_uses_aio_sandbox_from_env(local_shell_backend: None) -> None:
    assert uses_aio_sandbox() is False


@pytest.mark.asyncio
async def test_create_agent_backend_local_shell(
    local_shell_backend: None,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    monkeypatch.setattr(
        "agent.backends.factory.skills_root",
        lambda: tmp_path / "skills",
    )
    (tmp_path / "skills").mkdir()

    backend = await create_agent_backend("u1", "s1")
    assert "/skills/" in backend.routes
    assert "/user-skills/" in backend.routes
    ws = ensure_workspace_dir("u1", "s1")
    result = backend.write("/notes.md", "hello")
    assert result.error is None
    assert ws.joinpath("notes.md").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_create_agent_backend_aio(aio_backend: None) -> None:
    with patch(
        "agent.backends.factory.create_aio_agent_backend",
        new_callable=AsyncMock,
        return_value="composite-backend",
    ) as mock_create:
        got = await create_agent_backend("u1", "s1")
    mock_create.assert_awaited_once_with("u1", "s1")
    assert got == "composite-backend"
