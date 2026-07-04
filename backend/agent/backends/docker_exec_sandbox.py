"""DockerExecSandboxBackend：经 sandbox-runner docker exec 接入 deepagents BaseSandbox。"""

from __future__ import annotations

import base64

import httpx

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
from config.env import SandboxConfig, sandbox_runner_headers


class DockerExecSandboxBackend(BaseSandbox):
    """用户 docker 沙箱：runner 经 docker exec 执行，容器内绝对路径由 agent_filesystem 映射。"""

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._user_id = user_id
        self._session_id = session_id
        self._session_workspace = f"/workspace/sessions/{session_id}/workspace"
        self._mutex = session_mutex(user_id, session_id)
        self._default_timeout = float(SandboxConfig.execute_timeout_seconds)
        self._runner_url = SandboxConfig.runner_url.rstrip("/")
        self._http = http_client

    @property
    def id(self) -> str:
        return f"docker-{self._user_id}-{self._session_id}"

    def _post(self, path: str, payload: dict) -> httpx.Response:
        headers = sandbox_runner_headers()
        if self._http is not None:
            return self._http.post(
                f"{self._runner_url}{path}",
                headers=headers,
                json=payload,
            )
        timeout = self._default_timeout + 30.0
        with httpx.Client(timeout=timeout) as client:
            return client.post(
                f"{self._runner_url}{path}",
                headers=headers,
                json=payload,
            )

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective_timeout = float(timeout if timeout is not None else self._default_timeout)
        with self._mutex:
            try:
                resp = self._post(
                    f"/internal/sandboxes/{self._user_id}/exec",
                    {
                        "command": command,
                        "exec_dir": self._session_workspace,
                        "timeout": effective_timeout,
                    },
                )
            except httpx.HTTPError as exc:
                return ExecuteResponse(
                    output=f"Docker sandbox execute failed: {exc}",
                    exit_code=1,
                    truncated=False,
                )
        if resp.status_code >= 400:
            detail = resp.text.strip() or resp.reason_phrase
            return ExecuteResponse(
                output=f"Docker sandbox execute failed HTTP {resp.status_code}: {detail}",
                exit_code=1,
                truncated=False,
            )
        data = resp.json()
        return ExecuteResponse(
            output=str(data.get("output", "")),
            exit_code=int(data.get("exit_code", 1) or 0),
            truncated=bool(data.get("truncated", False)),
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        with self._mutex:
            for path, content in files:
                try:
                    abs_path = resolve_write_container_path(path)
                except ValueError:
                    responses.append(FileUploadResponse(path=path, error="invalid_path"))
                    continue
                text, encoding = prepare_write_file_payload(content)
                payload: dict[str, object] = {"file": abs_path, "content": text}
                if encoding is not None:
                    payload["encoding"] = encoding
                try:
                    resp = self._post(
                        f"/internal/sandboxes/{self._user_id}/files/write",
                        payload,
                    )
                    if resp.status_code >= 400:
                        responses.append(
                            FileUploadResponse(path=path, error="permission_denied")
                        )
                    else:
                        responses.append(FileUploadResponse(path=path, error=None))
                except httpx.HTTPError:
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
                    resp = self._post(
                        f"/internal/sandboxes/{self._user_id}/files/read",
                        {"file": abs_path},
                    )
                except httpx.HTTPError:
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="file_not_found")
                    )
                    continue
                if resp.status_code == 400 and "is_directory" in resp.text:
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="is_directory")
                    )
                    continue
                if resp.status_code >= 400:
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="file_not_found")
                    )
                    continue
                data = resp.json()
                text = str(data.get("content", ""))
                encoding = data.get("encoding")
                if encoding == "base64":
                    content = base64.b64decode(text)
                else:
                    content = text.encode("utf-8")
                responses.append(
                    FileDownloadResponse(path=path, content=content, error=None)
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


async def create_docker_exec_sandbox_backend(
    user_id: str,
    session_id: str,
) -> DockerExecSandboxBackend:
    from services.sandbox_service import ensure_user_sandbox

    await ensure_user_sandbox(user_id)
    return DockerExecSandboxBackend(user_id=user_id, session_id=session_id)
