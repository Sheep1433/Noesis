from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import server.main as server_main


@asynccontextmanager
async def _session_context():
    yield MagicMock()


def _patch_lifespan_resources(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    pg_manager = MagicMock()
    pg_manager.get_async_session_context.return_value = _session_context()
    pg_manager.close = AsyncMock()
    monkeypatch.setattr(server_main, "pg_manager", pg_manager)

    async_names = (
        "init_database",
        "init_checkpointer",
        "close_checkpointer",
        "init_knowledge_base",
        "close_knowledge_base",
        "shutdown_sandboxes",
        "sync_existing_kb_collection_configs",
        "stop_scheduled_task_scheduler",
        "stop_memory_dream_scheduler",
        "stop_telegram_runtime",
        "stop_feishu_runtime",
    )
    patched: dict[str, object] = {"pg_manager": pg_manager}
    for name in async_names:
        mock = AsyncMock(return_value=True)
        monkeypatch.setattr(server_main, name, mock)
        patched[name] = mock

    for name in (
        "start_scheduled_task_scheduler",
        "start_memory_dream_scheduler",
        "start_telegram_runtime",
        "start_feishu_runtime",
    ):
        mock = MagicMock()
        monkeypatch.setattr(server_main, name, mock)
        patched[name] = mock

    recover = AsyncMock()
    monkeypatch.setattr(server_main.RunRecoveryService, "recover_orphaned_runs", recover)
    patched["recover"] = recover
    shutdown_run_manager = AsyncMock()
    monkeypatch.setattr(server_main.run_manager, "shutdown", shutdown_run_manager)
    patched["shutdown_run_manager"] = shutdown_run_manager
    return patched


@pytest.mark.asyncio
async def test_lifespan_releases_resources_when_app_body_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_lifespan_resources(monkeypatch)

    with pytest.raises(RuntimeError, match="request failed"):
        async with server_main.lifespan(server_main.app):
            raise RuntimeError("request failed")

    for name in (
        "stop_feishu_runtime",
        "stop_telegram_runtime",
        "stop_memory_dream_scheduler",
        "stop_scheduled_task_scheduler",
        "shutdown_run_manager",
        "close_knowledge_base",
        "close_checkpointer",
        "shutdown_sandboxes",
    ):
        patched[name].assert_awaited_once()
    patched["pg_manager"].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_cleans_initialized_resources_when_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_lifespan_resources(monkeypatch)
    patched["init_knowledge_base"].side_effect = RuntimeError("qdrant init failed")

    with pytest.raises(RuntimeError, match="qdrant init failed"):
        async with server_main.lifespan(server_main.app):
            pass

    patched["close_checkpointer"].assert_awaited_once()
    patched["shutdown_sandboxes"].assert_awaited_once()
    patched["pg_manager"].close.assert_awaited_once()
    patched["close_knowledge_base"].assert_not_awaited()
    patched["stop_scheduled_task_scheduler"].assert_not_awaited()
