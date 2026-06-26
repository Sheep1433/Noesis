"""LocalShellBackend 工厂：为 Agent execute 提供可发现 gh/curl 等 CLI 的环境。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from config.skills_catalog import is_platform_skill_entry

_CONTAINER_WORKSPACE = "/workspace"
_PATH_PREFIXES: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/home/linuxbrew/.linuxbrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/sbin",
)

# 继承宿主环境时剔除密钥，降低 execute 误读/外泄风险。
_ENV_DENYLIST_EXACT: frozenset[str] = frozenset(
    {
        "JWT_SECRET_KEY",
        "MYSQL_PASSWORD",
        "MODEL_API_KEY",
        "EMBEDDING_MODEL_API_KEY",
        "RERANK_MODEL_API_KEY",
        "VLM_MODEL_API_KEY",
        "QDRANT_API_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "TAVILY_API_KEY",
    }
)

_ENV_DENYLIST_PATTERN = re.compile(
    r"(?:SECRET|PASSWORD|API_KEY|PRIVATE_KEY|TOKEN)(?:$|_)",
    re.IGNORECASE,
)

# gh 认证与常见 CLI 依赖；GH_TOKEN / GITHUB_TOKEN 保留供无交互场景使用。
_ENV_ALLOWLIST_EXACT: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TMP",
        "TEMP",
        "TERM",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


def _should_strip_env_key(key: str) -> bool:
    if key in _ENV_ALLOWLIST_EXACT:
        return False
    if key in _ENV_DENYLIST_EXACT:
        return True
    return bool(_ENV_DENYLIST_PATTERN.search(key))


def _merge_path(existing: str | None) -> str:
    parts: list[str] = list(_PATH_PREFIXES)
    if existing:
        parts.extend(p for p in existing.split(os.pathsep) if p)
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            ordered.append(part)
    return os.pathsep.join(ordered)


def build_shell_execute_env() -> dict[str, str]:
    """构建 Agent shell 子进程环境：保留 CLI 所需变量，剔除常见密钥。"""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if value is None or _should_strip_env_key(key):
            continue
        env[key] = value

    home = env.get("HOME") or os.path.expanduser("~")
    env["HOME"] = home
    env["PATH"] = _merge_path(env.get("PATH"))
    env.setdefault("USER", os.environ.get("USER", "appuser"))
    env.setdefault("LANG", os.environ.get("LANG", "C.UTF-8"))
    return env


def create_local_shell_backend(
    root_dir: str | Path,
    *,
    virtual_mode: bool = True,
    timeout: int | None = None,
    max_output_bytes: int | None = None,
) -> LocalShellBackend:
    """创建带过滤宿主环境的 LocalShellBackend，供深度研究 / 故障运维等 Agent 复用。"""
    kwargs: dict = {
        "root_dir": root_dir,
        "virtual_mode": virtual_mode,
        "env": build_shell_execute_env(),
        "inherit_env": False,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if max_output_bytes is not None:
        kwargs["max_output_bytes"] = max_output_bytes
    return LocalShellBackend(**kwargs)


def container_path_to_user_disk(path: str) -> str:
    """容器 `/workspace/...` → 用户盘 virtual `/sessions/...`（相对 `users/{uid}/`）。"""
    if not path.startswith("/"):
        msg = "Path must be absolute under /workspace"
        raise ValueError(msg)
    if path.startswith(f"{_CONTAINER_WORKSPACE}/"):
        return "/" + path[len(_CONTAINER_WORKSPACE) + 1 :]
    if path == _CONTAINER_WORKSPACE:
        return "/"
    msg = f"Path must start with {_CONTAINER_WORKSPACE}/"
    raise ValueError(msg)


class UserDiskBackend:
    """本地调试：与 AIO 相同，工具层使用 `/workspace/...` 绝对路径。"""

    def __init__(self, *, user_id: str, inner: LocalShellBackend) -> None:
        self._user_id = user_id
        self._inner = inner

    def _map(self, path: str) -> str:
        return container_path_to_user_disk(path)

    def _block_platform_skill_write(self, container_path: str) -> WriteResult | None:
        normalized = container_path
        prefix = f"{_CONTAINER_WORKSPACE}/skills/"
        if not normalized.startswith(prefix):
            return None
        rel = normalized[len(prefix) :].split("/", 1)[0]
        if is_platform_skill_entry(self._user_id, rel):
            return WriteResult(error="Platform skill symlink is read-only")
        return None

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if timeout is not None:
            return self._inner.execute(command, timeout=timeout)
        return self._inner.execute(command)

    def ls(self, path: str) -> LsResult:
        return self._inner.ls(self._map(path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._inner.read(self._map(file_path), offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        blocked = self._block_platform_skill_write(file_path)
        if blocked is not None:
            return blocked
        return self._inner.write(self._map(file_path), content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        blocked = self._block_platform_skill_write(file_path)
        if blocked is not None:
            return EditResult(error=blocked.error)
        return self._inner.edit(
            self._map(file_path), old_string, new_string, replace_all=replace_all
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        mapped = self._map(path) if path is not None else path
        return self._inner.grep(pattern, mapped, glob)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        mapped = self._map(path) if path != "/" else path
        return self._inner.glob(pattern, mapped or "/")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        mapped = []
        for path, content in files:
            try:
                mapped.append((self._map(path), content))
            except ValueError:
                mapped.append((path, content))
        results = self._inner.upload_files(
            [(p, c) for p, c in mapped if p.startswith("/")]
        )
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._inner.download_files([self._map(p) for p in paths])


def create_user_disk_backend(
    user_id: str,
    *,
    root_dir: str | Path,
    timeout: int | None = None,
    max_output_bytes: int | None = None,
) -> UserDiskBackend:
    inner = create_local_shell_backend(
        root_dir,
        virtual_mode=True,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    return UserDiskBackend(user_id=str(user_id), inner=inner)
