"""sandbox-runner：内网 user 级 AIO 沙箱 lifecycle API。"""

from __future__ import annotations

import os
import threading
import time

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from manager import SandboxManager

RUNNER_TOKEN = os.environ.get("SANDBOX_RUNNER_TOKEN", "")
RUNNER_HOST = os.environ.get("SANDBOX_RUNNER_HOST", "0.0.0.0")
RUNNER_PORT = int(os.environ.get("SANDBOX_RUNNER_PORT", "8090"))
REAP_INTERVAL = int(os.environ.get("SANDBOX_REAP_INTERVAL_SECONDS", "60"))

app = FastAPI(title="Noesis Sandbox Runner", docs_url=None, redoc_url=None)
_manager: SandboxManager | None = None
_reap_stop = threading.Event()


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


class EnsureResponse(BaseModel):
    base_url: str


class InFlightRequest(BaseModel):
    delta: int


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.put("/internal/sandboxes/{user_id}", dependencies=[Depends(verify_token)])
def ensure_sandbox(user_id: str) -> EnsureResponse:
    try:
        base_url = get_manager().ensure(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EnsureResponse(base_url=base_url)


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


def _reap_loop() -> None:
    while not _reap_stop.wait(REAP_INTERVAL):
        try:
            removed = get_manager().reap_idle()
            if removed:
                print(f"[sandbox-runner] idle 回收 {removed} 个用户沙箱")
        except Exception as exc:  # noqa: BLE001
            print(f"[sandbox-runner] idle 回收失败: {exc}")


@app.on_event("startup")
def on_startup() -> None:
    global _manager
    _manager = SandboxManager()
    thread = threading.Thread(target=_reap_loop, name="sandbox-reaper", daemon=True)
    thread.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    _reap_stop.set()
    if _manager is not None:
        _manager.shutdown_all()


if __name__ == "__main__":
    uvicorn.run(app, host=RUNNER_HOST, port=RUNNER_PORT, log_level="info")
