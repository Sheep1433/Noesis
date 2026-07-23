"""Agent 路径常量与归一：与容器/Shell 对齐的绝对坐标系。

权威路径（Agent 与 execute 一致）：
- 工作区 ``/workspace/...``
- Skills ``/skills/public/...``、``/skills/personal/...``
- 记忆 ``/memory/...``

UI ``sessions/{sid}/workspace/...`` 仅在注入 Agent 前映射一次。
**SHALL NOT** 再使用「filesystem 虚拟根 ``/notes.md``」坐标系。
"""

from __future__ import annotations

from pathlib import PurePosixPath

# ── 容器挂载 / Agent 路由常量 ──────────────────────────────────────────

WORKSPACE_CONTAINER_PREFIX = "/workspace"
PUBLIC_SKILLS_CONTAINER_PREFIX = "/skills/public"
PERSONAL_SKILLS_CONTAINER_PREFIX = "/skills/personal"

AGENT_PUBLIC_SKILLS_ROUTE = "/skills/public/"
AGENT_PERSONAL_SKILLS_ROUTE = "/skills/personal/"

AGENT_MEMORY_ROUTE = "/memory/"
AGENT_MEMORY_AGENTS_FILE = "/memory/AGENTS.md"
AGENT_MEMORY_USER_FILE = "/memory/USER.md"

READ_ONLY_SKILLS_ERROR = "Skills directory is read-only"

_ALLOWED_READ_PREFIXES = (
    WORKSPACE_CONTAINER_PREFIX,
    PUBLIC_SKILLS_CONTAINER_PREFIX,
    PERSONAL_SKILLS_CONTAINER_PREFIX,
)

_READ_ONLY_PREFIXES = (
    PUBLIC_SKILLS_CONTAINER_PREFIX,
    PERSONAL_SKILLS_CONTAINER_PREFIX,
)

# 已是绝对命名空间的根；canonicalize 不得再套 /workspace
_ABSOLUTE_ROOTS = (
    "/workspace",
    "/skills",
    "/memory",
    "/uploads",
    "/attachments",
)


def canonicalize_agent_path(path: str) -> str:
    """将 Agent/UI 侧路径归一为绝对权威路径。

    - ``notes.md`` / ``/notes.md`` → ``/workspace/notes.md``
    - ``/workspace/notes.md`` 保持；折叠 ``/workspace/workspace/...``
    - ``sessions/{sid}/workspace/x`` → ``/workspace/x``
    - ``/skills/...``、``/memory/...`` 等保持
    """
    text = (path or "").strip().replace("\\", "/")
    if not text or text == ".":
        return WORKSPACE_CONTAINER_PREFIX

    if not text.startswith("/"):
        text = f"/{text}"

    # sessions/<id>/workspace/... → /workspace/...
    parts = [p for p in text.split("/") if p]
    while len(parts) >= 3 and parts[0] == "sessions" and parts[2] == "workspace":
        parts = ["workspace", *parts[3:]]
    text = "/" + "/".join(parts) if parts else "/"

    # 已是绝对根下：只折叠多余 /workspace 前缀
    for root in _ABSOLUTE_ROOTS:
        if text == root or text.startswith(f"{root}/"):
            if root == WORKSPACE_CONTAINER_PREFIX:
                text = _collapse_workspace_prefix(text)
            return posix_clean(text)

    # 裸路径视为工作区相对
    rel = text.lstrip("/")
    return posix_clean(
        f"{WORKSPACE_CONTAINER_PREFIX}/{rel}" if rel else WORKSPACE_CONTAINER_PREFIX
    )


def _collapse_workspace_prefix(path: str) -> str:
    ws = WORKSPACE_CONTAINER_PREFIX
    text = path
    while text.startswith(f"{ws}{ws}/") or text == f"{ws}{ws}":
        text = text[len(ws) :] or ws
    # /workspace/workspace/x → /workspace/x
    while text.startswith(f"{ws}/workspace/") or text == f"{ws}/workspace":
        text = ws + text[len(f"{ws}/workspace") :]
    return text


def posix_clean(path: str) -> str:
    """PurePosixPath 规范化；禁止 ``..`` / ``~`` 穿越。"""
    if ".." in path.split("/") or path.startswith("~"):
        raise ValueError("Path traversal not allowed")
    cleaned = str(PurePosixPath(path))
    if not cleaned.startswith("/"):
        return f"/{cleaned}"
    return cleaned


def normalize_container_path(key: str) -> str:
    if not key.startswith("/"):
        raise ValueError("Path must be absolute")
    return posix_clean(key)


def assert_readable_container_path(container_path: str) -> None:
    for prefix in _ALLOWED_READ_PREFIXES:
        if container_path == prefix or container_path.startswith(f"{prefix}/"):
            return
    raise ValueError(f"Path outside sandbox mounts: {container_path}")


def resolve_read_container_path(key: str) -> str:
    container = normalize_container_path(key)
    assert_readable_container_path(container)
    return container


def _is_under_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def resolve_write_container_path(key: str) -> str:
    container = normalize_container_path(key)
    for prefix in _READ_ONLY_PREFIXES:
        if _is_under_prefix(container, prefix):
            raise ValueError(READ_ONLY_SKILLS_ERROR)
    if not _is_under_prefix(container, WORKSPACE_CONTAINER_PREFIX):
        raise ValueError(f"Path outside /workspace mount: {container}")
    return container


def strip_prefix(path: str, prefix: str) -> str:
    """``/workspace/a`` + prefix ``/workspace`` → ``/a``（供 local FilesystemBackend）。"""
    p = prefix.rstrip("/")
    if path == p:
        return "/"
    if path.startswith(f"{p}/"):
        return "/" + path[len(p) + 1 :]
    return path


def join_prefix(rel: str, prefix: str) -> str:
    p = prefix.rstrip("/")
    r = (rel or "/").lstrip("/")
    return f"{p}/{r}" if r else f"{p}/"
