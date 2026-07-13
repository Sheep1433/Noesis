"""ensure() runtime 对齐：标签与请求不一致时重建容器。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from manager import SandboxManager, SandboxRecord, _container_name


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> SandboxManager:
    monkeypatch.setenv("SANDBOX_RUNTIME", "docker")
    mock_client = MagicMock()
    mock_client.containers.list.return_value = []
    with patch("manager.docker.from_env", return_value=mock_client):
        mgr = SandboxManager()
    mgr._docker = mock_client
    return mgr


def test_ensure_recreates_when_synced_runtime_mismatch(
    manager: SandboxManager,
) -> None:
    stale = SandboxRecord(
        user_id="u1",
        container_id="old",
        container_name="noesis-sandbox-deadbeef",
        runtime="aio",
        base_url="http://127.0.0.1:18080",
    )
    fresh = SandboxRecord(
        user_id="u1",
        container_id="new",
        container_name="noesis-sandbox-deadbeef",
        runtime="docker",
        base_url=None,
    )
    manager._sync_running = MagicMock(return_value=stale)  # type: ignore[method-assign]
    manager._stop_and_remove = MagicMock(return_value=True)  # type: ignore[method-assign]
    manager._cleanup_stale_container = MagicMock()  # type: ignore[method-assign]
    manager._start_container = MagicMock(return_value=fresh)  # type: ignore[method-assign]

    result = manager.ensure("u1", runtime="docker")

    manager._stop_and_remove.assert_called_once_with("noesis-sandbox-deadbeef")
    manager._start_container.assert_called_once_with("u1", runtime="docker")
    assert result.runtime == "docker"


def test_cleanup_stale_container_removes_exited(manager: SandboxManager) -> None:
    exited = MagicMock()
    exited.status = "exited"
    manager._docker.containers.get.return_value = exited
    manager._stop_and_remove = MagicMock(return_value=True)  # type: ignore[method-assign]

    manager._cleanup_stale_container("u1")

    manager._stop_and_remove.assert_called_once_with(_container_name("u1"))


def test_cleanup_stale_container_skips_running(manager: SandboxManager) -> None:
    running = MagicMock()
    running.status = "running"
    manager._docker.containers.get.return_value = running
    manager._stop_and_remove = MagicMock()  # type: ignore[method-assign]

    manager._cleanup_stale_container("u1")

    manager._stop_and_remove.assert_not_called()


def test_ensure_removes_exited_container_before_create(
    manager: SandboxManager,
) -> None:
    fresh = SandboxRecord(
        user_id="u1",
        container_id="new",
        container_name="noesis-sandbox-deadbeef",
        runtime="docker",
        base_url=None,
    )
    manager._sync_running = MagicMock(return_value=None)  # type: ignore[method-assign]
    manager._cleanup_stale_container = MagicMock()  # type: ignore[method-assign]
    manager._start_container = MagicMock(return_value=fresh)  # type: ignore[method-assign]

    result = manager.ensure("u1", runtime="docker")

    manager._cleanup_stale_container.assert_called_once_with("u1")
    manager._start_container.assert_called_once_with("u1", runtime="docker")
    assert result is fresh


def test_ensure_reuses_when_runtime_matches(manager: SandboxManager) -> None:
    synced = SandboxRecord(
        user_id="u1",
        container_id="cid",
        container_name="noesis-sandbox-deadbeef",
        runtime="docker",
        base_url=None,
    )
    manager._sync_running = MagicMock(return_value=synced)  # type: ignore[method-assign]
    manager._stop_and_remove = MagicMock()  # type: ignore[method-assign]
    manager._start_container = MagicMock()  # type: ignore[method-assign]

    result = manager.ensure("u1", runtime="docker")

    manager._stop_and_remove.assert_not_called()
    manager._start_container.assert_not_called()
    assert result is synced
