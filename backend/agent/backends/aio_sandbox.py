"""AioSandboxBackend：经 agent_sandbox SDK 接入 deepagents BaseSandbox。"""

from __future__ import annotations

import base64
import hashlib
import shlex
from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    LsResult,
)
from deepagents.backends.sandbox import BaseSandbox

from agent.backends.sandbox_common import prepare_write_file_payload, session_mutex
from agent.backends.sandbox_mount_policy import (
    resolve_read_container_path,
    resolve_write_container_path,
)
from config.env import SandboxConfig

if TYPE_CHECKING:
    from agent_sandbox import Sandbox


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
    """用户 AIO 沙箱：仅处理容器内绝对路径（由 agent_filesystem 映射）。"""

    def __init__(
        self,
        *,
        base_url: str,
        user_id: str,
        session_id: str,
        inject_browser_env: bool = True,
        client: Sandbox | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                from agent_sandbox import Sandbox
            except ImportError as exc:
                raise ImportError(
                    "sandbox.backend=aio 需要可选依赖 agent-sandbox："
                    "在 backend 目录执行 uv sync --extra aio"
                ) from exc
            self._client = Sandbox(
                base_url=base_url, timeout=float(SandboxConfig.execute_timeout_seconds)
            )
        self._base_url = base_url
        self._user_id = user_id
        self._session_id = session_id
        self._session_workspace = f"/workspace/sessions/{session_id}/workspace"
        self._inject_browser_env = inject_browser_env
        self._mutex = session_mutex(user_id, session_id)
        self._default_timeout = SandboxConfig.execute_timeout_seconds

    @property
    def id(self) -> str:
        return f"aio-{self._user_id}-{self._session_id}"

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
                    exec_dir=self._session_workspace,
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
                    abs_path = resolve_write_container_path(path)
                except ValueError:
                    responses.append(FileUploadResponse(path=path, error="invalid_path"))
                    continue
                try:
                    text, encoding = prepare_write_file_payload(content)
                    write_kwargs: dict[str, object] = {
                        "file": abs_path,
                        "content": text,
                    }
                    if encoding is not None:
                        write_kwargs["encoding"] = encoding
                    self._client.file.write_file(**write_kwargs)
                    responses.append(FileUploadResponse(path=path, error=None))
                except Exception:
                    responses.append(FileUploadResponse(path=path, error="permission_denied"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        with self._mutex:
            for path in paths:
                try:
                    abs_path = resolve_read_container_path(path)
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

    def ls(self, path: str) -> LsResult:
        return super().ls(resolve_read_container_path(path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> object:
        return super().read(
            resolve_read_container_path(file_path), offset=offset, limit=limit
        )

    def write(self, file_path: str, content: str) -> object:
        return super().write(resolve_write_container_path(file_path), content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> object:
        return super().edit(
            resolve_write_container_path(file_path),
            old_string,
            new_string,
            replace_all=replace_all,
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> object:
        resolved = (
            self._session_workspace
            if path is None
            else resolve_read_container_path(path)
        )
        return super().grep(pattern, path=resolved, glob=glob)

    def glob(self, pattern: str, path: str = "/") -> object:
        resolved = (
            self._session_workspace
            if path == "/"
            else resolve_read_container_path(path)
        )
        return super().glob(pattern, path=resolved)


async def create_aio_sandbox_backend(user_id: str, session_id: str) -> AioSandboxBackend:
    """创建裸 AIO 沙箱 backend（容器路径由 agent_filesystem 映射）。"""
    from services.sandbox_service import ensure_user_sandbox

    handle = await ensure_user_sandbox(user_id)
    if handle.runtime != "aio" or not handle.base_url:
        raise RuntimeError(
            f"用户沙箱 runtime={handle.runtime!r}，aio backend 需要 base_url"
        )
    return AioSandboxBackend(
        base_url=handle.base_url,
        user_id=user_id,
        session_id=session_id,
        inject_browser_env=True,
    )
