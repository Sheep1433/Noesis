"""sandbox-runner：内网 user 级沙箱 lifecycle + docker exec 代理 API。"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager

import uvicorn
from docker.errors import DockerException
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from docker_exec import (
    exec_command,
    read_file_text,
    write_file_text,
)
from manager import SandboxManager

RUNNER_TOKEN = os.environ.get("SANDBOX_RUNNER_TOKEN", "")
RUNNER_HOST = os.environ.get("SANDBOX_RUNNER_HOST", "0.0.0.0")
RUNNER_PORT = int(os.environ.get("SANDBOX_RUNNER_PORT", "8090"))
REAP_INTERVAL = int(os.environ.get("SANDBOX_REAP_INTERVAL_SECONDS", str(3600)))

_manager: SandboxManager | None = None
_reap_stop = threading.Event()


def _reap_loop() -> None:
    while not _reap_stop.wait(REAP_INTERVAL):
        try:
            removed = get_manager().reap_idle()
            if removed:
                print(f"[sandbox-runner] idle 回收 {removed} 个用户沙箱")
        except Exception as exc:  # noqa: BLE001
            print(f"[sandbox-runner] idle 回收失败: {exc}")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _manager
    _manager = SandboxManager()
    thread = threading.Thread(target=_reap_loop, name="sandbox-reaper", daemon=True)
    thread.start()
    yield
    _reap_stop.set()
    if _manager is not None:
        _manager.shutdown_all()


app = FastAPI(
    title="Noesis Sandbox Runner",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)


def get_manager() -> SandboxManager:
    global _manager
    if _manager is None:
        _manager = SandboxManager()
    return _manager


def verify_token(authorization: str | None = Header(default=None)) -> None:
    if not RUNNER_TOKEN:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未授权")
    token = authorization.removeprefix("Bearer ").strip()
    if token != RUNNER_TOKEN:
        raise HTTPException(status_code=401, detail="未授权")


class EnsureRequest(BaseModel):
    runtime: str = Field(
        description="期望的沙箱 runtime：docker | aio（须与 backend sandbox.backend 一致）"
    )


class EnsureResponse(BaseModel):
    runtime: str
    container_name: str
    base_url: str | None = None


class InFlightRequest(BaseModel):
    delta: int


class ExecRequest(BaseModel):
    command: str
    exec_dir: str | None = None
    timeout: float | None = None


class ExecResponse(BaseModel):
    output: str
    exit_code: int
    truncated: bool = False


class FileReadRequest(BaseModel):
    file: str


class FileReadResponse(BaseModel):
    content: str
    encoding: str | None = None


class FileWriteRequest(BaseModel):
    file: str
    content: str
    encoding: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.put("/internal/sandboxes/{user_id}", dependencies=[Depends(verify_token)])
def ensure_sandbox(user_id: str, body: EnsureRequest) -> EnsureResponse:
    try:
        record = get_manager().ensure(user_id, runtime=body.runtime)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker 错误: {exc}") from exc
    return EnsureResponse(
        runtime=record.runtime,
        container_name=record.container_name,
        base_url=record.base_url,
    )


@app.delete("/internal/sandboxes/{user_id}", dependencies=[Depends(verify_token)])
def destroy_sandbox(user_id: str) -> dict[str, bool]:
    try:
        removed = get_manager().destroy(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"removed": removed}


@app.post(
    "/internal/sandboxes/{user_id}/in-flight",
    dependencies=[Depends(verify_token)],
)
def adjust_in_flight(user_id: str, body: InFlightRequest) -> dict[str, str]:
    try:
        get_manager().set_in_flight(user_id, body.delta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post(
    "/internal/sandboxes/{user_id}/exec",
    dependencies=[Depends(verify_token)],
)
def sandbox_exec(user_id: str, body: ExecRequest) -> ExecResponse:
    try:
        container = get_manager().get_container(user_id)
        result = exec_command(
            container,
            command=body.command,
            exec_dir=body.exec_dir,
            timeout=body.timeout,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExecResponse(
        output=result.output,
        exit_code=result.exit_code,
        truncated=result.truncated,
    )


@app.post(
    "/internal/sandboxes/{user_id}/files/read",
    dependencies=[Depends(verify_token)],
)
def sandbox_read_file(user_id: str, body: FileReadRequest) -> FileReadResponse:
    try:
        container = get_manager().get_container(user_id)
        content, encoding = read_file_text(container, path=body.file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="is_directory") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileReadResponse(content=content, encoding=encoding)


@app.post(
    "/internal/sandboxes/{user_id}/files/write",
    dependencies=[Depends(verify_token)],
)
def sandbox_write_file(user_id: str, body: FileWriteRequest) -> dict[str, str]:
    try:
        container = get_manager().get_container(user_id)
        write_file_text(
            container,
            path=body.file,
            content=body.content,
            encoding=body.encoding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host=RUNNER_HOST, port=RUNNER_PORT, log_level="info")
