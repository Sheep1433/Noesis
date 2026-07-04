"""容器内路径校验（AIO / docker exec 共用）。"""

from __future__ import annotations

from pathlib import PurePosixPath

from agent.backends.mount_paths import (
    CUSTOM_SKILLS_CONTAINER_PREFIX,
    EXTENSIONS_SKILLS_CONTAINER_PREFIX,
)

_CONTAINER_WORKSPACE = "/workspace"
_ALLOWED_READ_PREFIXES = (
    _CONTAINER_WORKSPACE,
    EXTENSIONS_SKILLS_CONTAINER_PREFIX,
)


def normalize_container_path(key: str) -> str:
    if not key.startswith("/"):
        msg = "Path must be absolute"
        raise ValueError(msg)
    if ".." in key or key.startswith("~"):
        msg = "Path traversal not allowed"
        raise ValueError(msg)
    return str(PurePosixPath(key))


def assert_readable_container_path(container_path: str) -> None:
    normalized = str(PurePosixPath(container_path))
    for prefix in _ALLOWED_READ_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return
    msg = f"Path outside sandbox mounts: {container_path}"
    raise ValueError(msg)


def resolve_read_container_path(key: str) -> str:
    container = normalize_container_path(key)
    assert_readable_container_path(container)
    return container


def resolve_write_container_path(key: str) -> str:
    container = normalize_container_path(key)
    if container.startswith(f"{EXTENSIONS_SKILLS_CONTAINER_PREFIX}/") or container == (
        EXTENSIONS_SKILLS_CONTAINER_PREFIX
    ):
        msg = "Platform skills are read-only"
        raise ValueError(msg)
    if not (
        container == _CONTAINER_WORKSPACE
        or container.startswith(f"{_CONTAINER_WORKSPACE}/")
    ):
        msg = f"Path outside /workspace mount: {container}"
        raise ValueError(msg)
    if container.startswith(f"{CUSTOM_SKILLS_CONTAINER_PREFIX}/") or container == (
        CUSTOM_SKILLS_CONTAINER_PREFIX
    ):
        msg = "Skills directory is read-only for agents"
        raise ValueError(msg)
    return container
