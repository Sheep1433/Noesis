"""LangGraph SQLite checkpointer 生命周期测试。"""
from __future__ import annotations

import pytest

from config.checkpointer import (
    close_checkpointer,
    get_checkpointer,
    init_checkpointer,
    resolve_checkpoint_db_path,
)


@pytest.mark.asyncio
async def test_sqlite_checkpointer_init_and_roundtrip(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "checkpoints.sqlite"
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DB_PATH", str(db_path))

    from config.env import get_config

    get_config.get_checkpoint_config.cache_clear()

    await close_checkpointer()
    saver = await init_checkpointer()
    assert resolve_checkpoint_db_path() == db_path.resolve()
    assert get_checkpointer() is saver

    config = {"configurable": {"thread_id": "test-thread-1", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1,
        "ts": "2026-06-11T00:00:00Z",
        "id": "chk-1",
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
    }
    saved = await saver.aput(config, checkpoint, {}, {})
    loaded = await saver.aget_tuple(saved)
    assert loaded is not None
    assert loaded.checkpoint["id"] == "chk-1"

    await close_checkpointer()
    get_config.get_checkpoint_config.cache_clear()
