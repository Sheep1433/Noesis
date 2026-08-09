import asyncio
from types import SimpleNamespace

import pytest

from evals.agent.harbor.harbor_backend import HarborBackend


class FakeHarborEnvironment:
    session_id = "trial-1"

    async def exec(self, command, *, cwd=None, timeout_sec=None):
        return SimpleNamespace(stdout=f"{cwd}:{command}", stderr="", return_code=0)


@pytest.mark.asyncio
async def test_harbor_backend_supports_async_and_threaded_sync_execution():
    backend = HarborBackend(
        FakeHarborEnvironment(),
        loop=asyncio.get_running_loop(),
        cwd="/workspace",
    )

    direct = await backend.aexecute("pwd", timeout=10)
    threaded = await asyncio.to_thread(backend.execute, "ls")

    assert direct.output == "/workspace:pwd"
    assert threaded.output == "/workspace:ls"


@pytest.mark.asyncio
async def test_harbor_backend_rejects_sync_execution_on_harbor_loop():
    backend = HarborBackend(
        FakeHarborEnvironment(),
        loop=asyncio.get_running_loop(),
        cwd="/workspace",
    )

    with pytest.raises(RuntimeError, match="outside the Harbor event loop"):
        backend.execute("pwd")
