"""sandbox.backend 工厂：local_shell / docker 统一 create_agent_backend。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from noesis.agents.backends import create_agent_backend, sandbox_backend_kind, uses_container_sandbox
from noesis.config.user_data_paths import ensure_workspace_dir
from deepagents.backends.composite import CompositeBackend


def _force_sandbox_backend(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    """直接替换配置读取方法（sandbox.backend 已无环境变量开关）。"""
    from noesis.config.env import GetConfig

    monkeypatch.setattr(
        GetConfig,
        "get_sandbox_config",
        lambda self: SimpleNamespace(
            backend=backend,
            runner_url="http://127.0.0.1:8090",
            execute_timeout_seconds=120,
        ),
    )


@pytest.fixture
def local_shell_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_sandbox_backend(monkeypatch, "local_shell")


@pytest.fixture
def docker_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_sandbox_backend(monkeypatch, "docker")


def test_uses_container_sandbox_follows_config(local_shell_backend: None) -> None:
    assert uses_container_sandbox() is False


def test_aio_backend_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_sandbox_backend(monkeypatch, "aio")
    with pytest.raises(ValueError, match="aio"):
        sandbox_backend_kind()


@pytest.mark.asyncio
async def test_create_agent_backend_local_shell(
    local_shell_backend: None,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noesis.config import user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(
        "noesis.agents.backends.factory.skills_root",
        lambda: platform,
    )

    backend = await create_agent_backend("u1", "s1")
    ws = ensure_workspace_dir("u1", "s1")
    target = "/workspace/research/notes.md"
    result = backend.write(target, "hello")
    assert result.error is None
    assert (ws / "research" / "notes.md").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_create_agent_backend_docker(docker_backend: None) -> None:
    with patch(
        "noesis.agents.backends.factory.create_docker_exec_sandbox_backend",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_sandbox = MagicMock()
        mock_create.return_value = mock_sandbox
        got = await create_agent_backend("u1", "s1")
    mock_create.assert_awaited_once_with("u1", "s1")
    assert isinstance(got, CompositeBackend)


def test_skill_sources_use_public_and_personal_routes() -> None:
    from noesis.agents.skills import SKILL_SOURCES
    from noesis.agents.backends.paths import (
        AGENT_PERSONAL_SKILLS_ROUTE,
        AGENT_PUBLIC_SKILLS_ROUTE,
    )

    assert SKILL_SOURCES == (
        (AGENT_PUBLIC_SKILLS_ROUTE, "Public"),
        (AGENT_PERSONAL_SKILLS_ROUTE, "Personal"),
    )
