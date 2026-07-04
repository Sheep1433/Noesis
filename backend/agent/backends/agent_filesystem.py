"""Agent 虚拟路径：`/research/` 工作区 + `/skills/extensions|custom/` 只读 Skills。"""

from __future__ import annotations

from pathlib import Path

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol

from agent.backends.backend_guards import StaticListingBackend, UserMemoryBackend
from agent.backends.local_shell import create_local_shell_backend
from agent.backends.mount_paths import (
    AGENT_CUSTOM_SKILLS_ROUTE,
    AGENT_EXTENSIONS_SKILLS_ROUTE,
    AGENT_MEMORY_ROUTE,
    AGENT_SKILLS_INDEX_ROUTE,
    CUSTOM_SKILLS_CONTAINER_PREFIX,
    EXTENSIONS_SKILLS_CONTAINER_PREFIX,
)
from agent.backends.path_rewrite import build_path_rewrite_context
from agent.backends.prefix_backend import PrefixBackend
from config.extensions_paths import skills_root
from config.user_data_paths import (
    get_user_agents_md_path,
    get_user_profile_md_path,
    get_user_skills_dir,
    get_workspace_dir,
)

# 测试与兼容 import
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
    """构建 Agent 文件系统：default=工作区，routes=extensions/custom skills（只读）。"""
    rewrite_ctx = build_path_rewrite_context(
        user_id=user_id,
        session_id=session_id,
        sandbox=sandbox,
    )

    if sandbox is not None:
        workspace = PrefixBackend(
            sandbox,
            container_prefix=f"/workspace/sessions/{session_id}/workspace",
            rewrite_ctx=rewrite_ctx,
        )
    else:
        workspace = PrefixBackend(
            create_local_shell_backend(
                get_workspace_dir(user_id, session_id),
                virtual_mode=True,
                timeout=shell_timeout,
            ),
            rewrite_ctx=rewrite_ctx,
        )

    return CompositeBackend(
        default=workspace,
        routes={
            AGENT_SKILLS_INDEX_ROUTE: StaticListingBackend(
                (
                    {"path": "/extensions/", "is_dir": True},
                    {"path": "/custom/", "is_dir": True},
                ),
                route=AGENT_SKILLS_INDEX_ROUTE,
            ),
            AGENT_EXTENSIONS_SKILLS_ROUTE: _skills_route_backend(
                sandbox=sandbox,
                host_root=skills_root(),
                container_prefix=EXTENSIONS_SKILLS_CONTAINER_PREFIX,
            ),
            AGENT_CUSTOM_SKILLS_ROUTE: _skills_route_backend(
                sandbox=sandbox,
                host_root=get_user_skills_dir(user_id),
                container_prefix=CUSTOM_SKILLS_CONTAINER_PREFIX,
            ),
            AGENT_MEMORY_ROUTE: UserMemoryBackend(
                agents_path=get_user_agents_md_path(user_id),
                user_path=get_user_profile_md_path(user_id),
            ),
        },
    )
