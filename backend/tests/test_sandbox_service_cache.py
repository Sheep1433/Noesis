"""Harness sandbox lifecycle：session 缓存失效与重建。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from noesis.agents.backends import sandbox_lifecycle as life

_LIFE = "noesis.agents.backends.sandbox_lifecycle"


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    life._HANDLE_CACHE.clear()
    monkeypatch.setenv("SANDBOX_SKIP_DOCKER_CHECK", "1")
    monkeypatch.setattr(
        f"{_LIFE}.SandboxConfig",
        SimpleNamespace(backend="docker", runner_url="http://sandbox-runner:8090"),
    )
    yield
    life._HANDLE_CACHE.clear()


@pytest.mark.asyncio
async def test_ensure_session_sandbox_caches_handle() -> None:
    resp = AsyncMock()
    resp.status_code = 200
    resp.json = lambda: {"runtime": "docker", "container_name": "c1"}
    resp.text = ""
    resp.reason_phrase = "ok"

    with (
        patch(f"{_LIFE}._runner_request", new_callable=AsyncMock, return_value=resp) as req,
        patch(f"{_LIFE}.ensure_user_root"),
        patch(f"{_LIFE}.ensure_user_skills_dir"),
        patch(f"{_LIFE}.ensure_workspace_dir"),
    ):
        h1 = await life.ensure_session_sandbox("u1", "s1")
        h2 = await life.ensure_session_sandbox("u1", "s1")

    assert h1.container_name == "c1"
    assert h1 is h2
    assert req.await_count == 1


@pytest.mark.asyncio
async def test_invalidate_forces_reensure() -> None:
    resp = AsyncMock()
    resp.status_code = 200
    resp.json = lambda: {"runtime": "docker", "container_name": "c2"}
    resp.text = ""
    resp.reason_phrase = "ok"

    with (
        patch(f"{_LIFE}._runner_request", new_callable=AsyncMock, return_value=resp) as req,
        patch(f"{_LIFE}.ensure_user_root"),
        patch(f"{_LIFE}.ensure_user_skills_dir"),
        patch(f"{_LIFE}.ensure_workspace_dir"),
    ):
        await life.ensure_session_sandbox("u1", "s1")
        life.invalidate_session_sandbox_cache("u1", "s1")
        await life.ensure_session_sandbox("u1", "s1")

    assert req.await_count == 2
