"""LocalShellBackend 工厂：为 Agent execute 提供可发现 gh/curl 等 CLI 的环境。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from deepagents.backends import LocalShellBackend

_PATH_PREFIXES: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/home/linuxbrew/.linuxbrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/sbin",
)

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
    """创建带过滤宿主环境的 LocalShellBackend。"""
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
