"""Agent 虚拟路径：workspace 根 + `/skills/public|personal/` + `/memory/`。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.backends.agent_filesystem import PrefixBackend, build_agent_filesystem_backend
from agent.backends.mount_paths import (
    AGENT_CUSTOM_SKILLS_ROUTE,
    AGENT_EXTENSIONS_SKILLS_ROUTE,
    AGENT_PERSONAL_SKILLS_ROUTE,
    AGENT_PUBLIC_SKILLS_ROUTE,
    PERSONAL_SKILLS_CONTAINER_PREFIX,
    PUBLIC_SKILLS_CONTAINER_PREFIX,
    WORKSPACE_CONTAINER_PREFIX,
)
from deepagents.backends.protocol import (
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
        self.write_calls: list[tuple[str, str]] = []

    def ls(self, path: str) -> LsResult:
        self.ls_calls.append(path)
        if path == "/":
            return LsResult(entries=[{"path": f"/{self.label}-file.md", "is_dir": False}])
        return LsResult(entries=[])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        self.read_calls.append(file_path)
        return ReadResult(file_data=FileData(content=self.label, encoding="utf-8"))

    def write(self, file_path: str, content: str) -> WriteResult:
        self.write_calls.append((file_path, content))
        return WriteResult(path=file_path)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        raise NotImplementedError

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None):
        raise NotImplementedError

    def glob(self, pattern: str, path: str = "/"):
        raise NotImplementedError


class _StubSandbox(_StubBackend, SandboxBackendProtocol):
    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.execute_calls: list[str] = []

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.execute_calls.append(command)
        return ExecuteResponse(output="ok", exit_code=0, truncated=False)

    @property
    def id(self) -> str:
        return "stub-sandbox"


def test_prefix_backend_maps_container_prefix() -> None:
    inner = _StubBackend("inner")
    backend = PrefixBackend(inner, container_prefix="/skills/public")
    backend.read("/deep-research-v2/SKILL.md")
    assert inner.read_calls == ["/skills/public/deep-research-v2/SKILL.md"]


def test_prefix_backend_map_out_strips_container_prefix() -> None:
    backend = PrefixBackend(_StubBackend("inner"), container_prefix="/skills/personal")
    assert backend._map_out("/skills/personal/my-tool") == "/my-tool"
    assert backend._map_out("/skills/personal") == "/"


def test_prefix_backend_execute_does_not_rewrite_shell_operators() -> None:
    sandbox = _StubSandbox("s")
    backend = PrefixBackend(sandbox, container_prefix="/workspace")
    cmd = "mkdir -p out && printf x > out/a.txt | cat"
    backend.execute(cmd)
    assert sandbox.execute_calls == [cmd]


@pytest.mark.asyncio
async def test_local_shell_composite_writes_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    (platform / "deep-research-v2").mkdir()
    (platform / "deep-research-v2" / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    backend = build_agent_filesystem_backend(
        user_id="u1",
        session_id="s1",
        sandbox=None,
        shell_timeout=30,
    )
    result = backend.write("/notes.md", "hello")
    assert result.error is None
    ws = user_paths.get_workspace_dir("u1", "s1")
    assert (ws / "notes.md").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_local_shell_execute_shell_operators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import user_data_paths as user_paths
    from config.agent_workspace_paths import ensure_workspace_dir

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )
    ensure_workspace_dir("u1", "s1")

    backend = build_agent_filesystem_backend(
        user_id="u1",
        session_id="s1",
        sandbox=None,
        shell_timeout=30,
    )
    result = backend.execute("mkdir -p out && printf done > out/result.txt")
    assert result.exit_code == 0, result.output
    ws = user_paths.get_workspace_dir("u1", "s1")
    assert (ws / "out" / "result.txt").read_text(encoding="utf-8") == "done"


@pytest.mark.asyncio
async def test_sandbox_composite_routes_and_workspace_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    sandbox = _StubSandbox("aio")
    backend = build_agent_filesystem_backend(
        user_id="u1",
        session_id="s1",
        sandbox=sandbox,
        shell_timeout=30,
    )
    assert AGENT_PUBLIC_SKILLS_ROUTE in backend.routes
    assert AGENT_PERSONAL_SKILLS_ROUTE in backend.routes
    assert AGENT_EXTENSIONS_SKILLS_ROUTE in backend.routes
    assert AGENT_CUSTOM_SKILLS_ROUTE in backend.routes

    workspace = backend.default
    assert isinstance(workspace, PrefixBackend)
    assert workspace._container_prefix == WORKSPACE_CONTAINER_PREFIX

    public = backend.routes[AGENT_PUBLIC_SKILLS_ROUTE]
    assert isinstance(public, PrefixBackend)
    assert public._container_prefix == PUBLIC_SKILLS_CONTAINER_PREFIX
    assert public._read_only is True

    personal = backend.routes[AGENT_PERSONAL_SKILLS_ROUTE]
    assert isinstance(personal, PrefixBackend)
    assert personal._container_prefix == PERSONAL_SKILLS_CONTAINER_PREFIX
    assert personal._read_only is True


@pytest.mark.asyncio
async def test_skills_routes_are_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    backend = build_agent_filesystem_backend(
        user_id="u1",
        session_id="s1",
        sandbox=None,
        shell_timeout=30,
    )
    result = backend.write("/skills/personal/my-skill/SKILL.md", "x")
    assert result.error is not None
    assert "read-only" in result.error.lower()


@pytest.mark.asyncio
async def test_legacy_skills_aliases_map_to_same_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(
        "agent.backends.agent_filesystem.skills_root",
        lambda: platform,
    )

    backend = build_agent_filesystem_backend(
        user_id="u1",
        session_id="s1",
        sandbox=_StubSandbox("s"),
        shell_timeout=30,
    )
    assert backend.routes[AGENT_EXTENSIONS_SKILLS_ROUTE] is backend.routes[AGENT_PUBLIC_SKILLS_ROUTE]
    assert backend.routes[AGENT_CUSTOM_SKILLS_ROUTE] is backend.routes[AGENT_PERSONAL_SKILLS_ROUTE]
