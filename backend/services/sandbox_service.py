"""用户级沙箱 lifecycle：经 sandbox-runner 创建容器并缓存句柄。"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Literal

import httpx

from common.logging import logger
from config.agent_workspace_paths import ensure_workspace_dir, ensure_user_root
from config.user_data_paths import ensure_user_skills_dir
from config.env import SandboxConfig, sandbox_runner_headers
from domain.chat.streaming.tool_errors import ToolInfrastructureError

SandboxRuntime = Literal["docker", "aio"]


@dataclass(frozen=True)
class UserSandboxHandle:
    runtime: SandboxRuntime
    container_name: str
    base_url: str | None = None


_HANDLE_CACHE: dict[str, UserSandboxHandle] = {}
_ENSURE_LOCKS: dict[str, asyncio.Lock] = {}


def _user_lock(user_id: str) -> asyncio.Lock:
    lock = _ENSURE_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _ENSURE_LOCKS[user_id] = lock
    return lock


def _docker_available() -> bool:
    if os.environ.get("SANDBOX_SKIP_DOCKER_CHECK", "").lower() in ("1", "true", "yes"):
        return True
    return os.path.exists("/var/run/docker.sock") or bool(
        os.environ.get("DOCKER_HOST")
    )


async def _runner_request(method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{SandboxConfig.runner_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            return await client.request(
                method, url, headers=sandbox_runner_headers(), **kwargs
            )
    except httpx.HTTPError as exc:
        raise ToolInfrastructureError(
            f"[INTERNAL_ERROR] sandbox-runner 不可达 ({SandboxConfig.runner_url}): {exc}"
        ) from exc


async def ensure_user_sandbox(user_id: str) -> UserSandboxHandle:
    """确保用户沙箱存在，返回 runner 句柄。"""
    if SandboxConfig.backend not in ("docker", "aio"):
        raise ToolInfrastructureError(
            f"[INTERNAL_ERROR] sandbox.backend={SandboxConfig.backend!r}，"
            "仅 docker/aio 模式经 runner 创建容器"
        )
    if not _docker_available():
        raise ToolInfrastructureError(
            "[INTERNAL_ERROR] 未检测到 Docker（/var/run/docker.sock），无法创建沙箱"
        )

    cached = _HANDLE_CACHE.get(user_id)
    expected_runtime: SandboxRuntime = SandboxConfig.backend  # type: ignore[assignment]
    if cached and cached.runtime != expected_runtime:
        _HANDLE_CACHE.pop(user_id, None)
        cached = None
    if cached:
        return cached

    async with _user_lock(user_id):
        cached = _HANDLE_CACHE.get(user_id)
        if cached and cached.runtime != expected_runtime:
            _HANDLE_CACHE.pop(user_id, None)
            cached = None
        if cached:
            return cached

        ensure_user_root(user_id)
        ensure_user_skills_dir(user_id)
        resp = await _runner_request(
            "PUT",
            f"/internal/sandboxes/{user_id}",
            json={"runtime": expected_runtime},
        )
        if resp.status_code == 401:
            raise ToolInfrastructureError("[INTERNAL_ERROR] sandbox-runner 鉴权失败")
        if resp.status_code >= 400:
            detail = resp.text.strip() or resp.reason_phrase
            raise ToolInfrastructureError(
                f"[INTERNAL_ERROR] 创建用户沙箱失败 HTTP {resp.status_code}: {detail}"
            )
        payload = resp.json()
        runtime = str(payload.get("runtime", "")).strip().lower()
        container_name = str(payload.get("container_name", "")).strip()
        if runtime not in ("docker", "aio"):
            raise ToolInfrastructureError(
                f"[INTERNAL_ERROR] sandbox-runner 返回未知 runtime={runtime!r}"
            )
        if runtime != expected_runtime:
            raise ToolInfrastructureError(
                f"[INTERNAL_ERROR] sandbox-runner runtime={runtime!r} "
                f"与 sandbox.backend={expected_runtime!r} 不一致"
            )
        if not container_name:
            raise ToolInfrastructureError("[INTERNAL_ERROR] sandbox-runner 未返回 container_name")
        base_url = payload.get("base_url")
        handle = UserSandboxHandle(
            runtime=runtime,  # type: ignore[arg-type]
            container_name=container_name,
            base_url=str(base_url).strip() if base_url else None,
        )
        _HANDLE_CACHE[user_id] = handle
        logger.info(
            "用户沙箱就绪 user_id={} runtime={} container={}",
            user_id,
            handle.runtime,
            handle.container_name,
        )
        return handle


async def destroy_user_sandbox(user_id: str) -> None:
    """销毁用户沙箱容器（磁盘 users/{uid}/ 保留）。"""
    _HANDLE_CACHE.pop(user_id, None)
    try:
        resp = await _runner_request("DELETE", f"/internal/sandboxes/{user_id}")
        if resp.status_code >= 400 and resp.status_code != 404:
            logger.warning(
                "destroy_user_sandbox 失败 user_id={} status={} body={}",
                user_id,
                resp.status_code,
                resp.text[:200],
            )
    except ToolInfrastructureError:
        logger.warning("destroy_user_sandbox runner 不可达 user_id={}", user_id)


async def _adjust_runner_in_flight(user_id: str, delta: int) -> None:
    try:
        await _runner_request(
            "POST",
            f"/internal/sandboxes/{user_id}/in-flight",
            json={"delta": delta},
        )
    except ToolInfrastructureError:
        pass


@asynccontextmanager
async def user_sandbox_run(user_id: str, session_id: str) -> AsyncIterator[UserSandboxHandle]:
    """Agent run 期间：ensure 沙箱、维护 in-flight、保证 workspace 存在。"""
    ensure_workspace_dir(user_id, session_id)
    handle = await ensure_user_sandbox(user_id)
    await _adjust_runner_in_flight(user_id, 1)
    try:
        yield handle
    finally:
        await _adjust_runner_in_flight(user_id, -1)


async def shutdown_sandboxes() -> None:
    """进程退出时销毁已缓存的全部用户沙箱。"""
    user_ids = list(_HANDLE_CACHE.keys())
    for user_id in user_ids:
        await destroy_user_sandbox(user_id)
