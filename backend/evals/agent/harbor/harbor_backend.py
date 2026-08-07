"""Map Harbor's async task environment to the Harness sandbox protocol."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Coroutine, TypeVar

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

T = TypeVar("T")


class HarborBackend(BaseSandbox):
    def __init__(
        self,
        environment: Any,
        *,
        loop: asyncio.AbstractEventLoop,
        cwd: str,
    ) -> None:
        self._environment = environment
        self._loop = loop
        self._cwd = cwd

    @property
    def id(self) -> str:
        return f"harbor:{self._environment.session_id}"

    def _wait(self, operation: Coroutine[object, object, T]) -> T:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            operation.close()
            raise RuntimeError(
                "HarborBackend sync methods must run outside the Harbor event loop"
            )
        return asyncio.run_coroutine_threadsafe(operation, self._loop).result()

    async def _execute(self, command: str, timeout: int | None) -> ExecuteResponse:
        result = await self._environment.exec(
            command,
            cwd=self._cwd,
            timeout_sec=timeout or 600,
        )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        return ExecuteResponse(
            output=output, exit_code=result.return_code, truncated=False
        )

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._wait(self._execute(command, timeout))

    async def aexecute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        return await self._execute(command, timeout)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        async def upload() -> list[FileUploadResponse]:
            responses: list[FileUploadResponse] = []
            with tempfile.TemporaryDirectory() as temp_dir:
                for index, (path, content) in enumerate(files):
                    source = Path(temp_dir) / str(index)
                    source.write_bytes(content)
                    try:
                        await self._environment.upload_file(source, path)
                        responses.append(FileUploadResponse(path=path, error=None))
                    except Exception as exc:  # noqa: BLE001
                        responses.append(FileUploadResponse(path=path, error=str(exc)))
            return responses

        return self._wait(upload())

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        async def download() -> list[FileDownloadResponse]:
            responses: list[FileDownloadResponse] = []
            with tempfile.TemporaryDirectory() as temp_dir:
                for index, path in enumerate(paths):
                    target = Path(temp_dir) / str(index)
                    try:
                        await self._environment.download_file(path, target)
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                content=target.read_bytes(),
                                error=None,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        responses.append(
                            FileDownloadResponse(
                                path=path, content=None, error=str(exc)
                            )
                        )
            return responses

        return self._wait(download())
