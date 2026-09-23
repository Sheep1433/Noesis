"""web 入口（worker-role-split）：无状态 HTTP 面 × N。

启动：NOESIS_RUN_BUS_BACKEND=redis ... uv run web.py
"""

import uvicorn

from noesis.config.env import AppConfig
from server.bootstrap.entries import build_web_app

app = build_web_app()


if __name__ == "__main__":
    uvicorn.run(
        app="web:app",
        host=AppConfig.app_host,
        port=AppConfig.app_port,
        root_path=AppConfig.app_root_path,
        reload=AppConfig.app_reload,
    )
