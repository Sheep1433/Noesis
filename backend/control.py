"""control 入口（worker-role-split）：调度器 / 通道 / 记忆 / 对账 × 1。

advisory lock 防双开（第二个实例启动失败退出）。/health 探针端口随
CONTROL_PORT 环境变量（默认 8091），与 web/worker 分离。
"""

import os

import uvicorn

from noesis.config.env import AppConfig
from server.bootstrap.entries import build_control_app

app = build_control_app()


if __name__ == "__main__":
    uvicorn.run(
        app="control:app",
        host=AppConfig.app_host,
        port=int(os.environ.get("CONTROL_PORT", "8091")),
    )
