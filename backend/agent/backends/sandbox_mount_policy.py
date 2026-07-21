"""容器内路径校验（docker exec / local 映射共用）。"""

from __future__ import annotations

from pathlib import PurePosixPath

from agent.backends.mount_paths import (
    PERSONAL_SKILLS_CONTAINER_PREFIX,
    PUBLIC_SKILLS_CONTAINER_PREFIX,
    WORKSPACE_CONTAINER_PREFIX,
)

_ALLOWED_READ_PREFIXES = (
    WORKSPACE_CONTAINER_PREFIX,
    PUBLIC_SKILLS_CONTAINER_PREFIX,
    PERSONAL_SKILLS_CONTAINER_PREFIX,
)

_READ_ONLY_PREFIXES = (
    PUBLIC_SKILLS_CONTAINER_PREFIX,
    PERSONAL_SKILLS_CONTAINER_PREFIX,
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


def _is_under_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def resolve_write_container_path(key: str) -> str:
    container = normalize_container_path(key)
    for prefix in _READ_ONLY_PREFIXES:
        if _is_under_prefix(container, prefix):
            msg = "Skills directory is read-only"
            raise ValueError(msg)
    if not _is_under_prefix(container, WORKSPACE_CONTAINER_PREFIX):
        msg = f"Path outside /workspace mount: {container}"
        raise ValueError(msg)
    return container
