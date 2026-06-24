"""Agent 沙箱后端：按 sandbox.backend 创建统一 CompositeBackend（workspace + skills 路由）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from deepagents.backends import CompositeBackend

from agent.backends.aio_sandbox import create_aio_agent_backend
from agent.backends.local_shell import create_local_shell_backend
from config.agent_workspace_paths import ensure_workspace_dir
from config.env import get_config
from config.extensions_paths import skills_root
from config.user_skills_paths import get_user_skills_dir
from services.sandbox_service import user_sandbox_run

SKILL_SOURCES: tuple[str, ...] = ("/skills/", "/user-skills/")

SandboxBackendKind = Literal["aio", "local_shell"]


def _sandbox_settings():
    return get_config.get_sandbox_config()


def sandbox_backend_kind() -> SandboxBackendKind:
    kind = (_sandbox_settings().backend or "aio").strip().lower()
    if kind not in ("aio", "local_shell"):
        raise ValueError(
            f"非法 sandbox.backend={_sandbox_settings().backend!r}，仅支持 aio | local_shell"
        )
    return kind  # type: ignore[return-value]


def uses_aio_sandbox() -> bool:
    return sandbox_backend_kind() == "aio"


def _shell_timeout() -> int:
    return _sandbox_settings().execute_timeout_seconds


def _create_local_workspace_backend(user_id: str, session_id: str):
    workspace = ensure_workspace_dir(user_id, session_id)
    return create_local_shell_backend(
        workspace,
        virtual_mode=True,
        timeout=_shell_timeout(),
    )


def _create_local_agent_backend(user_id: str, session_id: str) -> CompositeBackend:
    timeout = _shell_timeout()
    return CompositeBackend(
        default=_create_local_workspace_backend(user_id, session_id),
        routes={
            "/skills/": create_local_shell_backend(
                skills_root(), virtual_mode=True, timeout=timeout
            ),
            "/user-skills/": create_local_shell_backend(
                get_user_skills_dir(user_id), virtual_mode=True, timeout=timeout
            ),
        },
    )


@asynccontextmanager
async def agent_sandbox_session(user_id: str, session_id: str) -> AsyncIterator[None]:
    """AIO 模式维护 runner lifecycle；local_shell 仅确保 workspace 存在。"""
    ensure_workspace_dir(user_id, session_id)
    if uses_aio_sandbox():
        async with user_sandbox_run(user_id, session_id):
            yield
    else:
        yield


async def create_agent_backend(user_id: str, session_id: str) -> CompositeBackend:
    """创建 Agent 统一 backend：virtual `/` = session workspace，`/skills/` 与 `/user-skills/` 只读。"""
    if uses_aio_sandbox():
        return await create_aio_agent_backend(user_id, session_id)
    return _create_local_agent_backend(user_id, session_id)
