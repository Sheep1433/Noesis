"""Agent 虚拟路径：workspace 根 + `/skills/public|personal/` 只读 Skills + `/memory/`。"""

from __future__ import annotations

from pathlib import Path

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol

from agent.backends.backend_guards import UserMemoryBackend
from agent.backends.local_shell import create_local_shell_backend
from agent.backends.mount_paths import (
    AGENT_CUSTOM_SKILLS_ROUTE,
    AGENT_EXTENSIONS_SKILLS_ROUTE,
    AGENT_MEMORY_ROUTE,
    AGENT_PERSONAL_SKILLS_ROUTE,
    AGENT_PUBLIC_SKILLS_ROUTE,
    PERSONAL_SKILLS_CONTAINER_PREFIX,
    PUBLIC_SKILLS_CONTAINER_PREFIX,
    WORKSPACE_CONTAINER_PREFIX,
)
from agent.backends.prefix_backend import PrefixBackend
from config.extensions_paths import skills_root
from config.user_data_paths import (
    get_user_agents_md_path,
    get_user_profile_md_path,
    get_user_skills_dir,
    get_workspace_dir,
)

__all__ = (
    "PrefixBackend",
    "UserMemoryBackend",
    "build_agent_filesystem_backend",
)


def _skills_route_backend(
    *,
    sandbox: SandboxBackendProtocol | None,
    host_root: Path,
    container_prefix: str,
) -> PrefixBackend:
    inner: BackendProtocol = (
        sandbox
        if sandbox is not None
        else FilesystemBackend(root_dir=host_root, virtual_mode=True)
    )
    return PrefixBackend(
        inner,
        container_prefix=container_prefix if sandbox is not None else None,
        read_only=True,
    )


def build_agent_filesystem_backend(
    *,
    user_id: str,
    session_id: str,
    sandbox: SandboxBackendProtocol | None,
    shell_timeout: int,
) -> CompositeBackend:
    """构建 Agent 文件系统：default=工作区，routes=public/personal skills（只读）+ memory。"""
    if sandbox is not None:
        workspace = PrefixBackend(
            sandbox,
            container_prefix=WORKSPACE_CONTAINER_PREFIX,
        )
    else:
        workspace = PrefixBackend(
            create_local_shell_backend(
                get_workspace_dir(user_id, session_id),
                virtual_mode=True,
                timeout=shell_timeout,
            ),
        )

    public_skills = _skills_route_backend(
        sandbox=sandbox,
        host_root=skills_root(),
        container_prefix=PUBLIC_SKILLS_CONTAINER_PREFIX,
    )
    personal_skills = _skills_route_backend(
        sandbox=sandbox,
        host_root=get_user_skills_dir(user_id),
        container_prefix=PERSONAL_SKILLS_CONTAINER_PREFIX,
    )

    return CompositeBackend(
        default=workspace,
        routes={
            AGENT_PUBLIC_SKILLS_ROUTE: public_skills,
            AGENT_PERSONAL_SKILLS_ROUTE: personal_skills,
            # 过渡期 filesystem 别名
            AGENT_EXTENSIONS_SKILLS_ROUTE: public_skills,
            AGENT_CUSTOM_SKILLS_ROUTE: personal_skills,
            AGENT_MEMORY_ROUTE: UserMemoryBackend(
                agents_path=get_user_agents_md_path(user_id),
                user_path=get_user_profile_md_path(user_id),
            ),
        },
    )
