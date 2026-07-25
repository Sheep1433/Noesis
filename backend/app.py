import uvicorn
from noesis_server.server import app, AppConfig  # noqa: F401


if __name__ == '__main__':
    from noesis_server.bootstrap.sandbox_runner import ensure_sandbox_runner_process

    ensure_sandbox_runner_process()
    uvicorn.run(
        app='app:app',
        host=AppConfig.app_host,
        port=AppConfig.app_port,
        root_path=AppConfig.app_root_path,
        reload=AppConfig.app_reload,
    )
