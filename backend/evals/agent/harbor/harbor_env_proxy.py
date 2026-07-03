"""Harbor 任务容器环境代理（Harbor trial 进程内）。"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from harbor.environments.base import BaseEnvironment


class HarborEnvironmentProxy:
    def __init__(self, environment: BaseEnvironment) -> None:
        self._environment = environment
        self._server: asyncio.AbstractServer | None = None
        self.port: int = 0

    @property
    def url(self) -> str:
        return f"127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        sock = self._server.sockets[0]
        self.port = int(sock.getsockname()[1])

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    request = json.loads(line.decode("utf-8"))
                    response = await self._dispatch(request)
                except Exception as exc:  # noqa: BLE001
                    response = {"ok": False, "error": str(exc)}
                writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        op = request.get("op")
        if op == "exec":
            result = await self._environment.exec(
                command=str(request["command"]),
                cwd=request.get("cwd"),
                timeout_sec=int(request.get("timeout_sec") or 600),
            )
            return {
                "ok": True,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
                "return_code": result.return_code,
            }
        if op == "upload":
            content = base64.b64decode(str(request["content_b64"]))
            tmp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                await self._environment.upload_file(tmp_path, str(request["path"]))
                return {"ok": True}
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        if op == "download":
            tmp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp_path = tmp.name
                await self._environment.download_file(str(request["path"]), tmp_path)
                data = Path(tmp_path).read_bytes()
                return {"ok": True, "content_b64": base64.b64encode(data).decode("ascii")}
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        return {"ok": False, "error": f"unknown op: {op!r}"}
