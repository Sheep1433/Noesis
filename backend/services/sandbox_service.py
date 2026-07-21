"""会话级沙箱 lifecycle：经 sandbox-runner 创建容器并缓存句柄。"""

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

SandboxRuntime = Literal["docker"]


@dataclass(frozen=True)
class SessionSandboxHandle:
    runtime: SandboxRuntime
    container_name: str
    user_id: str
    session_id: str


# 兼容旧名
UserSandboxHandle = SessionSandboxHandle

_HANDLE_CACHE: dict[str, SessionSandboxHandle] = {}
_ENSURE_LOCKS: dict[str, asyncio.Lock] = {}


def _cache_key(user_id: str, session_id: str) -> str:
    return f"{user_id}:{session_id}"


def _session_lock(user_id: str, session_id: str) -> asyncio.Lock:
    key = _cache_key(user_id, session_id)
    lock = _ENSURE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ENSURE_LOCKS[key] = lock
    return lock


def invalidate_session_sandbox_cache(user_id: str, session_id: str) -> None:
    """清除 session 沙箱句柄缓存（runner 404 / 容器缺失时调用）。"""
    _HANDLE_CACHE.pop(_cache_key(user_id, session_id), None)


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


def _parse_ensure_response(
    resp: httpx.Response,
    *,
    user_id: str,
    session_id: str,
) -> SessionSandboxHandle:
    if resp.status_code == 401:
        raise ToolInfrastructureError("[INTERNAL_ERROR] sandbox-runner 鉴权失败")
    if resp.status_code >= 400:
        detail = resp.text.strip() or resp.reason_phrase
        raise ToolInfrastructureError(
            f"[INTERNAL_ERROR] 创建会话沙箱失败 HTTP {resp.status_code}: {detail}"
        )
    payload = resp.json()
    runtime = str(payload.get("runtime", "")).strip().lower()
    container_name = str(payload.get("container_name", "")).strip()
    if runtime != "docker":
        raise ToolInfrastructureError(
            f"[INTERNAL_ERROR] sandbox-runner 返回未知 runtime={runtime!r}"
        )
    if not container_name:
        raise ToolInfrastructureError("[INTERNAL_ERROR] sandbox-runner 未返回 container_name")
    return SessionSandboxHandle(
        runtime="docker",
        container_name=container_name,
        user_id=user_id,
        session_id=session_id,
    )


async def ensure_session_sandbox(user_id: str, session_id: str) -> SessionSandboxHandle:
    """确保会话沙箱存在，返回 runner 句柄。"""
    if SandboxConfig.backend != "docker":
        raise ToolInfrastructureError(
            f"[INTERNAL_ERROR] sandbox.backend={SandboxConfig.backend!r}，"
            "仅 docker 模式经 runner 创建容器"
        )
    if not _docker_available():
        raise ToolInfrastructureError(
            "[INTERNAL_ERROR] 未检测到 Docker（/var/run/docker.sock），无法创建沙箱"
        )

    key = _cache_key(user_id, session_id)
    cached = _HANDLE_CACHE.get(key)
    if cached:
        return cached

    async with _session_lock(user_id, session_id):
        cached = _HANDLE_CACHE.get(key)
        if cached:
            return cached

        ensure_user_root(user_id)
        ensure_user_skills_dir(user_id)
        ensure_workspace_dir(user_id, session_id)
        resp = await _runner_request(
            "PUT",
            f"/internal/sandboxes/{user_id}/sessions/{session_id}",
            json={"runtime": "docker"},
        )
        handle = _parse_ensure_response(resp, user_id=user_id, session_id=session_id)
        _HANDLE_CACHE[key] = handle
        logger.info(
            "会话沙箱就绪 user_id={} session_id={} container={}",
            user_id,
            session_id,
            handle.container_name,
        )
        return handle


# 兼容旧调用名
async def ensure_user_sandbox(user_id: str, session_id: str | None = None) -> SessionSandboxHandle:
    if not session_id:
        raise ToolInfrastructureError(
            "[INTERNAL_ERROR] ensure_user_sandbox 需要 session_id（已改为 per-session 沙箱）"
        )
    return await ensure_session_sandbox(user_id, session_id)


async def destroy_session_sandbox(user_id: str, session_id: str) -> None:
    """销毁会话沙箱容器（磁盘 workspace 保留或由调用方删除）。"""
    invalidate_session_sandbox_cache(user_id, session_id)
    try:
        resp = await _runner_request(
            "DELETE",
            f"/internal/sandboxes/{user_id}/sessions/{session_id}",
        )
        if resp.status_code >= 400 and resp.status_code != 404:
            logger.warning(
                "destroy_session_sandbox 失败 user_id={} session_id={} status={} body={}",
                user_id,
                session_id,
                resp.status_code,
                resp.text[:200],
            )
    except ToolInfrastructureError:
        logger.warning(
            "destroy_session_sandbox runner 不可达 user_id={} session_id={}",
            user_id,
            session_id,
        )


async def destroy_user_sandbox(user_id: str) -> None:
    """销毁该用户全部已缓存会话沙箱（进程退出 / 管理用途）。"""
    keys = [k for k in list(_HANDLE_CACHE.keys()) if k.startswith(f"{user_id}:")]
    for key in keys:
        _, session_id = key.split(":", 1)
        await destroy_session_sandbox(user_id, session_id)


async def _adjust_runner_in_flight(user_id: str, session_id: str, delta: int) -> None:
    try:
        await _runner_request(
            "POST",
            f"/internal/sandboxes/{user_id}/sessions/{session_id}/in-flight",
            json={"delta": delta},
        )
    except ToolInfrastructureError:
        pass


@asynccontextmanager
async def user_sandbox_run(
    user_id: str, session_id: str
) -> AsyncIterator[SessionSandboxHandle]:
    """Agent run 期间：ensure 沙箱、维护 in-flight、保证 workspace 存在。"""
    ensure_workspace_dir(user_id, session_id)
    handle = await ensure_session_sandbox(user_id, session_id)
    await _adjust_runner_in_flight(user_id, session_id, 1)
    try:
        yield handle
    finally:
        await _adjust_runner_in_flight(user_id, session_id, -1)


async def shutdown_sandboxes() -> None:
    """进程退出时销毁已缓存的全部会话沙箱。"""
    keys = list(_HANDLE_CACHE.keys())
    for key in keys:
        user_id, session_id = key.split(":", 1)
        await destroy_session_sandbox(user_id, session_id)
