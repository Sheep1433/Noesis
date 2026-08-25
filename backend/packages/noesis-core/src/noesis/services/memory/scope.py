"""Deterministic project and memory-scope identities."""

from __future__ import annotations

import hashlib
import re
import subprocess
from urllib.parse import urlsplit

from noesis.config.user_data_paths import get_workspace_dir


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_git_remote(value: str) -> str:
    remote = value.strip()
    scp = re.fullmatch(r"(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)", remote)
    if scp:
        remote = f"ssh://{scp.group('host')}/{scp.group('path')}"
    parsed = urlsplit(remote)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname.casefold()
        port = f":{parsed.port}" if parsed.port else ""
        path = re.sub(r"/+", "/", parsed.path).rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"{host}{port}{path.casefold()}"
    local = remote.rstrip("/")
    return local[:-4] if local.endswith(".git") else local


def resolve_project_key(user_id: str, session_id: str) -> str:
    """会话工作区内带 origin 的 Git 仓库 -> origin digest；其余一律 global。

    Agent 工作区是每会话沙箱：无 origin 的 Git 仓库若按沙箱路径 digest 生成
    scope，其他会话永远无法复现该 key，记忆会落入不可召回的死胡同 scope，
    因此归入 global（candidate 安全阀兜底）。
    """
    workspace = get_workspace_dir(user_id, session_id)
    if not (workspace / ".git").exists():
        return "global"
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    remote = result.stdout.strip() if result and result.returncode == 0 else ""
    return f"git-origin:{_digest(normalize_git_remote(remote))}" if remote else "global"


def build_scope_key(*, agent_profile: str, project_key: str) -> str:
    profile = re.sub(r"[^A-Za-z0-9_-]", "_", agent_profile.strip())[:80] or "unknown"
    project = project_key if project_key == "global" else project_key[:96]
    return f"profile:{profile}|project:{project}"


def resolve_scope_key(
    *, user_id: str, session_id: str, agent_profile: str
) -> str:
    return build_scope_key(
        agent_profile=agent_profile,
        project_key=resolve_project_key(user_id, session_id),
    )


__all__ = [
    "build_scope_key",
    "normalize_git_remote",
    "resolve_project_key",
    "resolve_scope_key",
]
