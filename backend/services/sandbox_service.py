"""用户级 AIO 沙箱 lifecycle：经 sandbox-runner 创建容器并缓存 base_url。"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

from common.logging import logger
from config.agent_workspace_paths import ensure_workspace_dir, ensure_user_root
from config.user_data_paths import ensure_user_skills_dir
from config.env import SandboxConfig, get_sandbox_runner_token
from domain.chat.streaming.tool_errors import ToolInfrastructureError

_BASE_URL_CACHE: dict[str, str] = {}
_ENSURE_LOCKS: dict[str, asyncio.Lock] = {}
_IN_FLIGHT: dict[str, int] = {}
_IN_FLIGHT_LOCK = asyncio.Lock()


def _runner_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = get_sandbox_runner_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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
            return await client.request(method, url, headers=_runner_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise ToolInfrastructureError(
            f"[INTERNAL_ERROR] sandbox-runner 不可达 ({SandboxConfig.runner_url}): {exc}"
        ) from exc


async def ensure_user_sandbox(user_id: str) -> str:
    """确保用户 AIO 沙箱存在，返回 base_url。"""
    if SandboxConfig.backend != "aio":
        raise ToolInfrastructureError(
            f"[INTERNAL_ERROR] sandbox.backend={SandboxConfig.backend!r}，"
            "仅 aio 模式经 runner 创建容器"
        )
    if not _docker_available():
        raise ToolInfrastructureError(
            "[INTERNAL_ERROR] 未检测到 Docker（/var/run/docker.sock），无法创建 AIO 沙箱"
        )

    cached = _BASE_URL_CACHE.get(user_id)
    if cached:
        return cached

    async with _user_lock(user_id):
        cached = _BASE_URL_CACHE.get(user_id)
        if cached:
            return cached

        ensure_user_root(user_id)
        ensure_user_skills_dir(user_id)
        resp = await _runner_request("PUT", f"/internal/sandboxes/{user_id}")
        if resp.status_code == 401:
            raise ToolInfrastructureError("[INTERNAL_ERROR] sandbox-runner 鉴权失败")
        if resp.status_code >= 400:
            detail = resp.text.strip() or resp.reason_phrase
            raise ToolInfrastructureError(
                f"[INTERNAL_ERROR] 创建用户沙箱失败 HTTP {resp.status_code}: {detail}"
            )
        payload = resp.json()
        base_url = str(payload.get("base_url", "")).strip()
        if not base_url:
            raise ToolInfrastructureError("[INTERNAL_ERROR] sandbox-runner 未返回 base_url")
        _BASE_URL_CACHE[user_id] = base_url
        logger.info("用户沙箱就绪 user_id={} base_url={}", user_id, base_url)
        return base_url


async def destroy_user_sandbox(user_id: str) -> None:
    """销毁用户 AIO 沙箱容器（磁盘 users/{uid}/ 保留）。"""
    _BASE_URL_CACHE.pop(user_id, None)
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


async def _notify_runner_in_flight(user_id: str, delta: int) -> None:
    try:
        await _runner_request(
            "POST",
            f"/internal/sandboxes/{user_id}/in-flight",
            json={"delta": delta},
        )
    except ToolInfrastructureError:
        pass


async def increment_in_flight(user_id: str) -> None:
    async with _IN_FLIGHT_LOCK:
        _IN_FLIGHT[user_id] = _IN_FLIGHT.get(user_id, 0) + 1
    await _notify_runner_in_flight(user_id, 1)


async def decrement_in_flight(user_id: str) -> None:
    async with _IN_FLIGHT_LOCK:
        current = _IN_FLIGHT.get(user_id, 0)
        _IN_FLIGHT[user_id] = max(0, current - 1)
        if _IN_FLIGHT[user_id] == 0:
            _IN_FLIGHT.pop(user_id, None)
    await _notify_runner_in_flight(user_id, -1)


def get_in_flight(user_id: str) -> int:
    return _IN_FLIGHT.get(user_id, 0)


@asynccontextmanager
async def user_sandbox_run(user_id: str, session_id: str) -> AsyncIterator[str]:
    """Agent run 期间：ensure 沙箱、维护 in-flight、保证 workspace 存在。"""
    ensure_workspace_dir(user_id, session_id)
    base_url = await ensure_user_sandbox(user_id)
    await increment_in_flight(user_id)
    try:
        yield base_url
    finally:
        await decrement_in_flight(user_id)


async def reap_idle_sandboxes() -> int:
    """将本地 in-flight 状态同步给 runner（runner 后台线程负责实际回收）。"""
    return 0


async def shutdown_sandboxes() -> None:
    """进程退出时销毁已缓存的全部用户沙箱。"""
    user_ids = list(_BASE_URL_CACHE.keys())
    for user_id in user_ids:
        await destroy_user_sandbox(user_id)
