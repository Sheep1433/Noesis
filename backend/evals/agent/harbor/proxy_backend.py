"""Proxy 模式 SandboxBackend（Worker 经 TCP 访问 Harbor 容器）。"""

from __future__ import annotations

import base64
import uuid

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from evals.agent.harbor.harbor_proxy_client import proxy_request


class ProxyHarborBackend(BaseSandbox):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        cwd: str = "/",
        default_timeout_sec: int = 600,
        session_id: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._cwd = cwd.rstrip("/") or "/"
        self._default_timeout_sec = default_timeout_sec
        self._session_id = session_id or str(uuid.uuid4())
        self._backend_id = f"harbor-proxy:{self._session_id}"

    @property
    def id(self) -> str:
        return self._backend_id

    def _rpc(self, payload: dict, *, timeout_sec: int) -> dict:
        return proxy_request(self._host, self._port, payload, timeout_sec=timeout_sec)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        timeout_sec = self._default_timeout_sec if timeout is None else timeout
        result = self._rpc(
            {
                "op": "exec",
                "command": command,
                "cwd": self._cwd,
                "timeout_sec": timeout_sec,
            },
            timeout_sec=timeout_sec,
        )
        parts = []
        if result.get("stdout"):
            parts.append(str(result["stdout"]))
        if result.get("stderr"):
            parts.append(str(result["stderr"]))
        return ExecuteResponse(
            output="\n".join(parts) if parts else "",
            exit_code=int(result.get("return_code") or 0),
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                self._rpc(
                    {
                        "op": "upload",
                        "path": path,
                        "content_b64": base64.b64encode(content).decode("ascii"),
                    },
                    timeout_sec=120,
                )
                responses.append(FileUploadResponse(path=path, error=None))
            except Exception as exc:  # noqa: BLE001
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                result = self._rpc({"op": "download", "path": path}, timeout_sec=120)
                content = base64.b64decode(str(result["content_b64"]))
                responses.append(
                    FileDownloadResponse(path=path, content=content, error=None)
                )
            except Exception:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )
        return responses
