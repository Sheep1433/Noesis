"""Agent 虚拟路径：`/research/` 工作区 + `/skills/extensions|custom/`。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.backends.agent_filesystem import PrefixBackend, build_agent_filesystem_backend
from agent.backends.mount_paths import (
    AGENT_CUSTOM_SKILLS_ROUTE,
    AGENT_EXTENSIONS_SKILLS_ROUTE,
    EXTENSIONS_SKILLS_CONTAINER_PREFIX,
)
from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileData,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)


class _StubBackend:
    def __init__(self, label: str) -> None:
        self.label = label
        self.read_calls: list[str] = []
        self.ls_calls: list[str] = []

    def ls(self, path: str) -> LsResult:
        self.ls_calls.append(path)
        if path == "/":
            return LsResult(entries=[{"path": f"/{self.label}-file.md", "is_dir": False}])
        return LsResult(entries=[])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        self.read_calls.append(file_path)
        return ReadResult(file_data=FileData(content=self.label, encoding="utf-8"))

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(path=file_path)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        raise NotImplementedError

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None):
        raise NotImplementedError

    def glob(self, pattern: str, path: str = "/"):
        raise NotImplementedError


class _StubSandbox(_StubBackend, SandboxBackendProtocol):
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output="ok", exit_code=0, truncated=False)

    @property
    def id(self) -> str:
        return "stub-sandbox"


def test_prefix_backend_maps_container_prefix() -> None:
    inner = _StubBackend("inner")
    backend = PrefixBackend(inner, container_prefix="/skills")
    backend.read("/deep-research-v2/SKILL.md")
    assert inner.read_calls == ["/skills/deep-research-v2/SKILL.md"]


def test_prefix_backend_read_only_rejects_write() -> None:
    inner = _StubBackend("inner")
    backend = PrefixBackend(inner, read_only=True)
    result = backend.write("/foo.md", "x")
    assert result.error is not None
    assert inner.read_calls == []


@pytest.mark.asyncio
async def test_prefix_backend_als_delegates_to_ls() -> None:
    inner = _StubBackend("inner")
    backend = PrefixBackend(inner, read_only=True)
    result = await backend.als("/")
    assert result.error is None
    assert inner.ls_calls == ["/"]


@pytest.mark.asyncio
async def test_create_agent_backend_local_shell_lists_extensions_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setenv("SANDBOX_BACKEND", "local_shell")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()

    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    skill_dir = platform / "deep-research-v2"
    skill_dir.mkdir()
    monkeypatch.setattr(user_paths, "_USERS_ROOT", users_root)
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    from agent.backends.factory import create_agent_backend

    backend = await create_agent_backend("u1", "s1")
    result = await backend.als(AGENT_EXTENSIONS_SKILLS_ROUTE)
    assert result.error is None
    paths = {entry["path"].rstrip("/") for entry in result.entries or []}
    assert "/skills/extensions/deep-research-v2" in paths


@pytest.mark.asyncio
async def test_create_agent_backend_local_shell_writes_research_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setenv("SANDBOX_BACKEND", "local_shell")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()

    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(user_paths, "_USERS_ROOT", users_root)
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    from agent.backends.factory import create_agent_backend

    backend = await create_agent_backend("u1", "s1")
    target = "/research/notes.md"
    result = backend.write(target, "hello")
    assert result.error is None
    ws = user_paths.get_workspace_dir("u1", "s1")
    assert (ws / "research" / "notes.md").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_create_agent_backend_reads_extensions_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setenv("SANDBOX_BACKEND", "local_shell")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()

    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    skill_dir = platform / "deep-research-v2"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("skill-body", encoding="utf-8")
    monkeypatch.setattr(user_paths, "_USERS_ROOT", users_root)
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    from agent.backends.factory import create_agent_backend

    backend = await create_agent_backend("u1", "s1")
    result = backend.read("/skills/extensions/deep-research-v2/SKILL.md")
    assert result.error is None
    assert result.file_data is not None
    content = (
        result.file_data.content
        if hasattr(result.file_data, "content")
        else result.file_data["content"]
    )
    assert "skill-body" in content


@pytest.mark.asyncio
async def test_create_agent_backend_custom_skills_route_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setenv("SANDBOX_BACKEND", "local_shell")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()

    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(user_paths, "_USERS_ROOT", users_root)
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    from agent.backends.factory import create_agent_backend

    backend = await create_agent_backend("u1", "s1")
    result = backend.write("/skills/custom/my-skill/SKILL.md", "x")
    assert result.error is not None


@pytest.mark.asyncio
async def test_skills_middleware_can_load_extensions_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config import user_data_paths as user_paths
    from deepagents.middleware.skills import _alist_skills_with_errors

    monkeypatch.setenv("SANDBOX_BACKEND", "local_shell")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()

    users_root = tmp_path / "users"
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    skill_dir = platform / "deep-research-v2"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deep-research-v2\ndescription: test skill\n---\n\n# Body\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(user_paths, "_USERS_ROOT", users_root)
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    from agent.backends.factory import create_agent_backend

    backend = await create_agent_backend("u1", "s1")
    skills, err = await _alist_skills_with_errors(backend, AGENT_EXTENSIONS_SKILLS_ROUTE)
    assert err is None
    assert len(skills) == 1
    assert skills[0]["name"] == "deep-research-v2"


@pytest.mark.asyncio
async def test_create_agent_backend_aio_uses_composite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, patch

    monkeypatch.setenv("SANDBOX_BACKEND", "aio")
    from config.env import get_config

    get_config.get_sandbox_config.cache_clear()

    with patch(
        "agent.backends.factory.create_aio_sandbox_backend",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_sandbox = MagicMock()
        mock_create.return_value = mock_sandbox
        from agent.backends.factory import create_agent_backend
        from deepagents.backends.composite import CompositeBackend

        backend = await create_agent_backend("u1", "s1")

    assert isinstance(backend, CompositeBackend)
    assert AGENT_EXTENSIONS_SKILLS_ROUTE in backend.routes
    assert AGENT_CUSTOM_SKILLS_ROUTE in backend.routes
    mock_create.assert_awaited_once_with("u1", "s1")


def test_build_agent_filesystem_aio_maps_extensions_container_prefix() -> None:
    sandbox = _StubSandbox("sandbox")
    backend = build_agent_filesystem_backend(
        user_id="u1",
        session_id="s1",
        sandbox=sandbox,  # type: ignore[arg-type]
        shell_timeout=30,
    )
    route_backend = backend.routes[AGENT_EXTENSIONS_SKILLS_ROUTE]
    assert isinstance(route_backend, PrefixBackend)
    route_backend.read("/deep-research-v2/SKILL.md")
    assert sandbox.read_calls == [
        f"{EXTENSIONS_SKILLS_CONTAINER_PREFIX}/deep-research-v2/SKILL.md"
    ]
