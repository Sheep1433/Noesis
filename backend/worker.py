"""worker 入口（worker-role-split）：run 认领执行面 × N。

水平扩展：副本数即执行容量（每 worker 认领分片）。/health 探针端口随
WORKER_PORT 环境变量（默认 8092）。
"""

import os

import uvicorn

from noesis.config.env import AppConfig
from server.bootstrap.entries import build_worker_app

app = build_worker_app()


if __name__ == "__main__":
    # 沙箱生命周期归 worker：runner 未运行时自动拉起（8090）
    from server.bootstrap.sandbox_runner import ensure_sandbox_runner_process

    ensure_sandbox_runner_process()
    uvicorn.run(
        app="worker:app",
        host=AppConfig.app_host,
        port=int(os.environ.get("WORKER_PORT", "8092")),
    )
