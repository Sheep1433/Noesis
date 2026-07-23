"""Agent 绝对路径坐标系：``/workspace`` + ``/skills/...`` + ``/memory/``。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.backends.factory import build_agent_filesystem_backend
from agent.backends.paths import (
    AGENT_MEMORY_ROUTE,
    AGENT_PERSONAL_SKILLS_ROUTE,
    AGENT_PUBLIC_SKILLS_ROUTE,
    WORKSPACE_CONTAINER_PREFIX,
)
from agent.backends.agent_path import AgentPathBackend
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
        self.write_calls: list[tuple[str, str]] = []

    def ls(self, path: str) -> LsResult:
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


def test_agent_path_backend_docker_passthrough_absolute() -> None:
    inner = _StubBackend("inner")
    backend = AgentPathBackend(inner)
    backend.write("/workspace/research/plan.md", "x")
    backend.write("/notes.md", "y")
    backend.write("sessions/s1/workspace/research/plan.md", "z")
    assert inner.write_calls == [
        ("/workspace/research/plan.md", "x"),
        ("/workspace/notes.md", "y"),
        ("/workspace/research/plan.md", "z"),
    ]


def test_agent_path_backend_local_strips_workspace() -> None:
    inner = _StubBackend("inner")
    backend = AgentPathBackend(inner, strip_root=WORKSPACE_CONTAINER_PREFIX)
    backend.write("/workspace/notes.md", "a")
    backend.write("/notes.md", "b")
    assert inner.write_calls == [
        ("/notes.md", "a"),
        ("/notes.md", "b"),
    ]


def test_agent_path_backend_execute_does_not_rewrite_shell() -> None:
    sandbox = _StubSandbox("s")
    backend = AgentPathBackend(sandbox)
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
        "agent.backends.factory.skills_root",
        lambda: platform,
    )

    backend = build_agent_filesystem_backend(
        user_id="u1",
        session_id="s1",
        sandbox=None,
        shell_timeout=30,
    )
    result = backend.write("/workspace/notes.md", "hello")
    assert result.error is None
    ws = user_paths.get_workspace_dir("u1", "s1")
    assert (ws / "notes.md").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_local_shell_execute_shell_operators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import user_data_paths as user_paths
    from config.user_data_paths import ensure_workspace_dir

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(
        "agent.backends.factory.skills_root",
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
async def test_docker_composite_skills_on_default_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(
        "agent.backends.factory.skills_root",
        lambda: platform,
    )

    sandbox = _StubSandbox("docker")
    backend = build_agent_filesystem_backend(
        user_id="u1",
        session_id="s1",
        sandbox=sandbox,
        shell_timeout=30,
    )
    assert AGENT_PUBLIC_SKILLS_ROUTE not in backend.routes
    assert AGENT_PERSONAL_SKILLS_ROUTE not in backend.routes
    assert AGENT_MEMORY_ROUTE in backend.routes
    assert isinstance(backend.default, AgentPathBackend)
    assert backend.default._strip_root is None

    backend.write("/workspace/a.md", "x")
    backend.read("/skills/public/demo/SKILL.md")
    assert sandbox.write_calls == [("/workspace/a.md", "x")]
    assert sandbox.read_calls == ["/skills/public/demo/SKILL.md"]


@pytest.mark.asyncio
async def test_local_skills_routes_are_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    platform = tmp_path / "platform-skills"
    platform.mkdir()
    monkeypatch.setattr(
        "agent.backends.factory.skills_root",
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
