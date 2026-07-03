"""Harbor 环境代理客户端（Worker 侧，无 harbor 包依赖）。"""

from __future__ import annotations

import json
import socket
from typing import Any


def proxy_request(host: str, port: int, payload: dict[str, Any], *, timeout_sec: int = 600) -> dict[str, Any]:
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    with socket.create_connection((host, port), timeout=timeout_sec + 10) as sock:
        sock.sendall(data)
        sock_file = sock.makefile("rb")
        line = sock_file.readline()
        if not line:
            raise RuntimeError("empty response from harbor environment proxy")
        response = json.loads(line.decode("utf-8"))
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "proxy request failed"))
        return response


async def resolve_container_working_dir_via_proxy(host: str, port: int) -> str:
    for candidate in ("/app", "/workspace", "/root", "/"):
        result = proxy_request(
            host,
            port,
            {
                "op": "exec",
                "command": f"test -d {candidate}",
                "cwd": "/",
                "timeout_sec": 10,
            },
            timeout_sec=15,
        )
        if int(result.get("return_code") or 1) == 0:
            return candidate
    return "/"
