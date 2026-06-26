"""Agent 沙箱后端：AIO 与 local_shell 均映射用户盘 `/workspace/...` 绝对路径。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal, Union

from agent.backends.aio_sandbox import AioSandboxBackend, create_aio_agent_backend
from agent.backends.local_shell import create_user_disk_backend
from config.agent_workspace_paths import ensure_workspace_dir
from config.env import get_config
from config.skills_catalog import ensure_user_skills_catalog
from config.user_data_paths import get_user_root
from services.sandbox_service import user_sandbox_run

# 平台 + 用户 skill 已合并至 `.data/users/{uid}/skills/`（容器内 `/workspace/skills/`）
SKILL_SOURCES: tuple[str, ...] = ("/workspace/skills/",)

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


def _create_local_agent_backend(user_id: str, session_id: str):
    ensure_user_skills_catalog(user_id)
    ensure_workspace_dir(user_id, session_id)
    return create_user_disk_backend(
        user_id,
        root_dir=get_user_root(user_id),
        timeout=_shell_timeout(),
    )


@asynccontextmanager
async def agent_sandbox_session(user_id: str, session_id: str) -> AsyncIterator[None]:
    """AIO 模式维护 runner lifecycle；local_shell 仅确保 workspace 存在。"""
    ensure_workspace_dir(user_id, session_id)
    ensure_user_skills_catalog(user_id)
    if uses_aio_sandbox():
        async with user_sandbox_run(user_id, session_id):
            yield
    else:
        yield


async def create_agent_backend(
    user_id: str, session_id: str
) -> Union[AioSandboxBackend, object]:
    """创建 Agent backend：virtual 根为 `.data/users/{uid}/`，工具路径用 `/workspace/...`。"""
    if uses_aio_sandbox():
        return await create_aio_agent_backend(user_id, session_id)
    return _create_local_agent_backend(user_id, session_id)
