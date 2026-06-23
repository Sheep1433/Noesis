"""AioSandboxBackend：经 agent_sandbox SDK 接入 deepagents BaseSandbox。"""

from __future__ import annotations

import hashlib
import shlex
import threading
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from config.env import SandboxConfig

if TYPE_CHECKING:
    from agent_sandbox import Sandbox

_MUTEX_REGISTRY: dict[tuple[str, str], threading.Lock] = {}
_MUTEX_REGISTRY_LOCK = threading.Lock()


def _session_mutex(user_id: str, session_id: str) -> threading.Lock:
    key = (user_id, session_id)
    with _MUTEX_REGISTRY_LOCK:
        lock = _MUTEX_REGISTRY.get(key)
        if lock is None:
            lock = threading.Lock()
            _MUTEX_REGISTRY[key] = lock
        return lock


def _cdp_port_for_session(session_id: str) -> int:
    digest = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16)
    return 9300 + (digest % 700)


def _session_browser_env(session_id: str) -> dict[str, str]:
    profile = f"/workspace/sessions/{session_id}/workspace/.chrome-profile"
    return {
        "SANDBOX_HEADLESS": "1",
        "BAOYU_CHROME_PROFILE_DIR": profile,
        "SANDBOX_CDP_PORT": str(_cdp_port_for_session(session_id)),
    }


class AioSandboxBackend(BaseSandbox):
    """用户 AIO 容器内的 BaseSandbox 适配层（virtual `/` = session workspace）。"""

    def __init__(
        self,
        *,
        base_url: str,
        user_id: str,
        session_id: str,
        root_dir: str,
        inject_browser_env: bool = True,
        client: Sandbox | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            from agent_sandbox import Sandbox

            self._client = Sandbox(
                base_url=base_url, timeout=float(SandboxConfig.execute_timeout_seconds)
            )
        self._base_url = base_url
        self._user_id = user_id
        self._session_id = session_id
        self._root_dir = root_dir.rstrip("/") or "/"
        self._inject_browser_env = inject_browser_env
        self._mutex = _session_mutex(user_id, session_id)
        self._default_timeout = SandboxConfig.execute_timeout_seconds

    @property
    def id(self) -> str:
        return f"aio-{self._user_id}-{self._session_id}"

    def _resolve_path(self, key: str) -> str:
        vpath = key if key.startswith("/") else f"/{key}"
        if ".." in vpath or vpath.startswith("~"):
            msg = "Path traversal not allowed"
            raise ValueError(msg)
        full = str(PurePosixPath(self._root_dir) / vpath.lstrip("/"))
        root = PurePosixPath(self._root_dir)
        resolved = PurePosixPath(full)
        if not str(resolved).startswith(str(root)):
            msg = f"Path:{full} outside root directory: {self._root_dir}"
            raise ValueError(msg)
        return full

    def _with_env(self, command: str) -> str:
        if not self._inject_browser_env:
            return command
        env = _session_browser_env(self._session_id)
        prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        return f"env {prefix} {command}"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        wrapped = self._with_env(command)
        with self._mutex:
            try:
                result = self._client.shell.exec_command(
                    command=wrapped,
                    exec_dir=self._root_dir,
                    timeout=float(effective_timeout),
                )
            except Exception as exc:
                return ExecuteResponse(
                    output=f"AIO sandbox execute failed: {exc}",
                    exit_code=1,
                    truncated=False,
                )
        data = getattr(result, "data", result)
        output = getattr(data, "output", "") or ""
        exit_code = int(getattr(data, "exit_code", getattr(data, "code", 1)) or 0)
        truncated = bool(getattr(data, "truncated", False))
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        with self._mutex:
            for path, content in files:
                try:
                    abs_path = self._resolve_path(path)
                except ValueError as exc:
                    responses.append(FileUploadResponse(path=path, error="invalid_path"))
                    continue
                try:
                    self._client.file.write_file(file=abs_path, content=content)
                    responses.append(FileUploadResponse(path=path, error=None))
                except Exception:
                    responses.append(FileUploadResponse(path=path, error="permission_denied"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        with self._mutex:
            for path in paths:
                try:
                    abs_path = self._resolve_path(path)
                except ValueError:
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="invalid_path")
                    )
                    continue
                try:
                    result = self._client.file.read_file(file=abs_path)
                    data = getattr(result, "data", result)
                    text = getattr(data, "content", None)
                    if text is None and hasattr(data, "output"):
                        text = data.output
                    if isinstance(text, str):
                        content = text.encode("utf-8")
                    elif isinstance(text, (bytes, bytearray)):
                        content = bytes(text)
                    else:
                        content = b""
                    responses.append(
                        FileDownloadResponse(path=path, content=content, error=None)
                    )
                except Exception as exc:
                    msg = str(exc).lower()
                    error = "is_directory" if "directory" in msg else "file_not_found"
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error=error)
                    )
        return responses

    def ls(self, path: str) -> object:
        return super().ls(self._resolve_path(path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> object:
        return super().read(self._resolve_path(file_path), offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> object:
        return super().write(self._resolve_path(file_path), content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> object:
        return super().edit(
            self._resolve_path(file_path),
            old_string,
            new_string,
            replace_all=replace_all,
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> object:
        resolved = self._resolve_path(path) if path else self._root_dir
        return super().grep(pattern, path=resolved, glob=glob)

    def glob(self, pattern: str, path: str = "/") -> object:
        return super().glob(pattern, path=self._resolve_path(path))


async def create_user_workspace_backend(user_id: str, session_id: str) -> AioSandboxBackend:
    """仅 session workspace 的 AIO backend（故障运维等）。"""
    from services.sandbox_service import ensure_user_sandbox

    base_url = await ensure_user_sandbox(user_id)
    return AioSandboxBackend(
        base_url=base_url,
        user_id=user_id,
        session_id=session_id,
        root_dir=f"/workspace/sessions/{session_id}/workspace",
        inject_browser_env=True,
    )


async def create_user_sandbox_backend(user_id: str, session_id: str):
    """创建 CompositeBackend：session workspace + 平台 /skills/ + 用户 /user-skills/。"""
    from deepagents.backends import CompositeBackend

    from services.sandbox_service import ensure_user_sandbox

    base_url = await ensure_user_sandbox(user_id)
    workspace_root = f"/workspace/sessions/{session_id}/workspace"
    workspace_backend = AioSandboxBackend(
        base_url=base_url,
        user_id=user_id,
        session_id=session_id,
        root_dir=workspace_root,
        inject_browser_env=True,
    )
    platform_skills_backend = AioSandboxBackend(
        base_url=base_url,
        user_id=user_id,
        session_id=session_id,
        root_dir="/skills",
        inject_browser_env=False,
    )
    user_skills_backend = AioSandboxBackend(
        base_url=base_url,
        user_id=user_id,
        session_id=session_id,
        root_dir="/workspace/skills",
        inject_browser_env=False,
    )
    return CompositeBackend(
        default=workspace_backend,
        routes={
            "/skills/": platform_skills_backend,
            "/user-skills/": user_skills_backend,
        },
    )
