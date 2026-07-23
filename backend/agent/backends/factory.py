"""Agent 沙箱后端：选 runtime + 组装 CompositeBackend。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Literal

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol

from agent.backends.agent_path import AgentPathBackend
from agent.backends.docker_exec import create_docker_exec_sandbox_backend
from agent.backends.local_shell import create_local_shell_backend
from agent.backends.memory import UserMemoryBackend
from agent.backends.paths import (
    AGENT_MEMORY_ROUTE,
    AGENT_PERSONAL_SKILLS_ROUTE,
    AGENT_PUBLIC_SKILLS_ROUTE,
    WORKSPACE_CONTAINER_PREFIX,
)
from config.env import get_config
from config.extensions_paths import skills_root
from config.user_data_paths import (
    ensure_user_memory_files,
    ensure_user_skills_dir,
    ensure_workspace_dir,
    get_user_agents_md_path,
    get_user_profile_md_path,
    get_user_skills_dir,
    get_workspace_dir,
)
from services.sandbox_service import user_sandbox_run

SandboxBackendKind = Literal["docker", "local_shell"]


def _sandbox_settings():
    return get_config.get_sandbox_config()


def sandbox_backend_kind() -> SandboxBackendKind:
    kind = (_sandbox_settings().backend or "docker").strip().lower()
    if kind == "aio":
        raise ValueError(
            "sandbox.backend=aio 已移除；请使用 docker（生产）或 local_shell（开发/测试）"
        )
    if kind not in ("docker", "local_shell"):
        raise ValueError(
            f"非法 sandbox.backend={_sandbox_settings().backend!r}，"
            "仅支持 docker | local_shell"
        )
    return kind  # type: ignore[return-value]


def uses_container_sandbox() -> bool:
    return sandbox_backend_kind() == "docker"


def _shell_timeout() -> int:
    return _sandbox_settings().execute_timeout_seconds


@asynccontextmanager
async def agent_sandbox_session(user_id: str, session_id: str) -> AsyncIterator[None]:
    """容器沙箱模式维护 runner lifecycle；local_shell 仅确保目录存在。"""
    ensure_user_memory_files(user_id)
    if uses_container_sandbox():
        async with user_sandbox_run(user_id, session_id):
            yield
    else:
        ensure_workspace_dir(user_id, session_id)
        ensure_user_skills_dir(user_id)
        yield


def _read_only_fs(host_root: Path) -> AgentPathBackend:
    """Composite 已剥 route 前缀；不再二次 canonicalize。"""
    return AgentPathBackend(
        FilesystemBackend(root_dir=host_root, virtual_mode=True),
        read_only=True,
        canonicalize=False,
    )


def build_agent_filesystem_backend(
    *,
    user_id: str,
    session_id: str,
    sandbox: SandboxBackendProtocol | None,
    shell_timeout: int,
) -> CompositeBackend:
    """docker：default=沙箱（含 skills 挂载）；local：workspace strip + skills routes。"""
    memory = UserMemoryBackend(
        agents_path=get_user_agents_md_path(user_id),
        user_path=get_user_profile_md_path(user_id),
    )

    if sandbox is not None:
        default: BackendProtocol = AgentPathBackend(sandbox)
        return CompositeBackend(
            default=default,
            routes={AGENT_MEMORY_ROUTE: memory},
        )

    default = AgentPathBackend(
        create_local_shell_backend(
            get_workspace_dir(user_id, session_id),
            virtual_mode=True,
            timeout=shell_timeout,
        ),
        strip_root=WORKSPACE_CONTAINER_PREFIX,
    )
    return CompositeBackend(
        default=default,
        routes={
            AGENT_PUBLIC_SKILLS_ROUTE: _read_only_fs(skills_root()),
            AGENT_PERSONAL_SKILLS_ROUTE: _read_only_fs(get_user_skills_dir(user_id)),
            AGENT_MEMORY_ROUTE: memory,
        },
    )


async def create_agent_backend(user_id: str, session_id: str) -> CompositeBackend:
    """选 sandbox runtime，再组装 workspace + skills + memory。"""
    sandbox: SandboxBackendProtocol | None = None
    kind = sandbox_backend_kind()
    if kind == "docker":
        sandbox = await create_docker_exec_sandbox_backend(user_id, session_id)
    return build_agent_filesystem_backend(
        user_id=user_id,
        session_id=session_id,
        sandbox=sandbox,
        shell_timeout=_shell_timeout(),
    )
