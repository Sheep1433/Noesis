"""Agent 虚拟路径 → 物理路径：供 workspace PrefixBackend.execute 单点 rewrite。"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from agent.backends.mount_paths import (
    AGENT_CUSTOM_SKILLS_ROUTE,
    AGENT_EXTENSIONS_SKILLS_ROUTE,
    AGENT_MEMORY_AGENTS_FILE,
    AGENT_MEMORY_USER_FILE,
    CUSTOM_SKILLS_CONTAINER_PREFIX,
    EXTENSIONS_SKILLS_CONTAINER_PREFIX,
    USER_DATA_CONTAINER_PREFIX,
)
from config.extensions_paths import skills_root
from config.user_data_paths import (
    get_user_agents_md_path,
    get_user_profile_md_path,
    get_user_skills_dir,
    get_workspace_dir,
)

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

_SYSTEM_DENYLIST: tuple[str, ...] = (
    "/usr",
    "/etc",
    "/tmp",
    "/bin",
    "/sbin",
    "/opt",
    "/var",
    "/home",
    "/dev",
    "/proc",
    "/sys",
)

_TIER1_ROUTES: tuple[tuple[str, str], ...] = (
    (AGENT_EXTENSIONS_SKILLS_ROUTE, "extensions"),
    (AGENT_CUSTOM_SKILLS_ROUTE, "custom"),
    ("/research/", "research"),
)


@dataclass(frozen=True, slots=True)
class PathRewriteContext:
    backend_kind: Literal["container", "local_shell"]
    user_id: str
    session_id: str
    workspace_prefix: str
    extensions_prefix: str
    custom_skills_prefix: str
    memory_agents_path: str
    memory_user_path: str


def build_path_rewrite_context(
    *,
    user_id: str,
    session_id: str,
    sandbox: SandboxBackendProtocol | None,
) -> PathRewriteContext:
    if sandbox is not None:
        return PathRewriteContext(
            backend_kind="container",
            user_id=user_id,
            session_id=session_id,
            workspace_prefix=f"/workspace/sessions/{session_id}/workspace",
            extensions_prefix=EXTENSIONS_SKILLS_CONTAINER_PREFIX,
            custom_skills_prefix=CUSTOM_SKILLS_CONTAINER_PREFIX,
            memory_agents_path=f"{USER_DATA_CONTAINER_PREFIX}/AGENTS.md",
            memory_user_path=f"{USER_DATA_CONTAINER_PREFIX}/USER.md",
        )
    return PathRewriteContext(
        backend_kind="local_shell",
        user_id=user_id,
        session_id=session_id,
        workspace_prefix=str(get_workspace_dir(user_id, session_id)),
        extensions_prefix=str(skills_root()),
        custom_skills_prefix=str(get_user_skills_dir(user_id)),
        memory_agents_path=str(get_user_agents_md_path(user_id)),
        memory_user_path=str(get_user_profile_md_path(user_id)),
    )


def _physical_prefix_for_tier1(route_kind: str, ctx: PathRewriteContext) -> str:
    if route_kind == "extensions":
        return ctx.extensions_prefix
    if route_kind == "custom":
        return ctx.custom_skills_prefix
    return f"{ctx.workspace_prefix}/research"


def _rewrite_path_token(token: str, ctx: PathRewriteContext) -> str:
    if token == AGENT_MEMORY_AGENTS_FILE:
        return ctx.memory_agents_path
    if token == AGENT_MEMORY_USER_FILE:
        return ctx.memory_user_path

    for route_prefix, route_kind in _TIER1_ROUTES:
        if token == route_prefix.rstrip("/"):
            return _physical_prefix_for_tier1(route_kind, ctx)
        if token.startswith(route_prefix):
            suffix = token[len(route_prefix) :]
            base = _physical_prefix_for_tier1(route_kind, ctx)
            return f"{base}/{suffix}" if suffix else base

    if not _is_tier2_workspace_root_path(token):
        return token
    return f"{ctx.workspace_prefix}{token}"


def _is_tier2_workspace_root_path(token: str) -> bool:
    if not token.startswith("/"):
        return False
    if token == "/skills" or (
        token.startswith("/skills/") and not token.startswith(AGENT_EXTENSIONS_SKILLS_ROUTE)
        and not token.startswith(AGENT_CUSTOM_SKILLS_ROUTE)
    ):
        return False
    if token.startswith("/memory/"):
        return False
    for prefix in _SYSTEM_DENYLIST:
        if token == prefix or token.startswith(f"{prefix}/"):
            return False
    return True


def rewrite_virtual_paths_in_command(command: str, *, ctx: PathRewriteContext) -> str:
    """Token 级虚拟路径 rewrite；禁止对整条 command 裸 str.replace。"""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return command

    rewritten: list[str] = []
    for token in tokens:
        if token.startswith("/"):
            rewritten.append(_rewrite_path_token(token, ctx))
        else:
            rewritten.append(token)
    return shlex.join(rewritten)
