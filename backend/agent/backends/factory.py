"""Agent 沙箱后端：CompositeBackend + `/research/` 工作区 + `/memory/` + `/skills/`。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.middleware.skills import SkillSource

from agent.backends.agent_filesystem import build_agent_filesystem_backend
from agent.backends.aio_sandbox import create_aio_sandbox_backend
from agent.backends.docker_exec_sandbox import create_docker_exec_sandbox_backend
from agent.backends.mount_paths import (
    AGENT_CUSTOM_SKILLS_ROUTE,
    AGENT_EXTENSIONS_SKILLS_ROUTE,
)
from config.agent_workspace_paths import ensure_workspace_dir
from config.env import get_config
from config.user_data_paths import ensure_user_memory_files, ensure_user_skills_dir
from services.sandbox_service import user_sandbox_run

SKILL_SOURCES: tuple[SkillSource, ...] = (
    (AGENT_EXTENSIONS_SKILLS_ROUTE, "Extensions"),
    (AGENT_CUSTOM_SKILLS_ROUTE, "Custom"),
)

SandboxBackendKind = Literal["docker", "aio", "local_shell"]


def _sandbox_settings():
    return get_config.get_sandbox_config()


def sandbox_backend_kind() -> SandboxBackendKind:
    kind = (_sandbox_settings().backend or "docker").strip().lower()
    if kind not in ("docker", "aio", "local_shell"):
        raise ValueError(
            f"非法 sandbox.backend={_sandbox_settings().backend!r}，"
            "仅支持 docker | aio | local_shell"
        )
    return kind  # type: ignore[return-value]


def uses_container_sandbox() -> bool:
    return sandbox_backend_kind() in ("docker", "aio")


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


async def create_agent_backend(user_id: str, session_id: str) -> CompositeBackend:
    """Agent 文件系统：session `/research/` + 用户 `/memory/` + 只读 Skills。"""
    sandbox: SandboxBackendProtocol | None = None
    kind = sandbox_backend_kind()
    if kind == "docker":
        sandbox = await create_docker_exec_sandbox_backend(user_id, session_id)
    elif kind == "aio":
        sandbox = await create_aio_sandbox_backend(user_id, session_id)
    return build_agent_filesystem_backend(
        user_id=user_id,
        session_id=session_id,
        sandbox=sandbox,
        shell_timeout=_shell_timeout(),
    )
