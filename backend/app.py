"""all-in-one 入口（本地自用默认形态）：单进程 web + control + worker。

总线按 NOESIS_RUN_BUS_BACKEND 选择：memory（默认推荐，零额外依赖）或
redis。生产/扩容形态用三入口 web.py / control.py / worker.py（redis 强制）。
"""

import uvicorn

from noesis.config.env import AppConfig
from server.bootstrap.entries import build_all_in_one_app

app = build_all_in_one_app()


if __name__ == "__main__":
    uvicorn.run(
        app="app:app",
        host=AppConfig.app_host,
        port=AppConfig.app_port,
        root_path=AppConfig.app_root_path,
        reload=AppConfig.app_reload,
    )
