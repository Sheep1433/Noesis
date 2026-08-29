"""子会话终态落库契约：统一管道的 usage 聚合写入 assistant 消息 extra.usage。

mark_terminal 经 AgentRunRepository.finalize 透传 usage——与主链路
RunService 的终态落库同构；本用例桩掉 DB 层验证透传不丢字段。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from noesis.chat.runs import RunStatus
from noesis.services.subagent_session_service import SubagentSessionService


@pytest.mark.asyncio
async def test_mark_terminal_passes_usage_to_finalize(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeRepo:
        def __init__(self, db):
            pass

        async def get(self, run_id):
            return SimpleNamespace(
                id=run_id,
                snapshot={"version": 1, "parts": []},
                last_sequence=3,
            )

        async def finalize(self, **kwargs):
            captured.update(kwargs)
            return True

    class _FakeDb:
        async def commit(self):
            pass

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeDb()

        async def __aexit__(self, *args):
            return False

    # 服务内为函数内局部导入：桩源模块属性（调用时读取）
    import noesis.repositories.agent_run_repository as repo_mod
    import noesis.storage.postgres.manager as pg_mod

    monkeypatch.setattr(repo_mod, "AgentRunRepository", _FakeRepo)
    monkeypatch.setattr(
        pg_mod, "pg_manager",
        SimpleNamespace(get_async_session_context=lambda: _FakeCtx()),
    )

    usage = {
        "steps": 5,
        "llm_ms": 1234.0,
        "input_tokens": 84000,
        "output_tokens": 2100,
        "cache_read_tokens": 66000,
        "cache_write_tokens": 0,
    }
    await SubagentSessionService.mark_terminal(
        run_id="run-u",
        status=RunStatus.COMPLETED,
        content={"version": 1, "parts": [{"type": "text", "content": "done"}]},
        finish_reason="stop",
        usage=usage,
    )

    assert captured["usage"] == usage
    assert captured["finish_reason"] == "stop"
    assert captured["target"] == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_mark_terminal_usage_defaults_to_none(monkeypatch) -> None:
    """非管道路径（取消/超时）不传 usage：落库保持 None，不写空统计。"""
    captured: dict[str, Any] = {}

    class _FakeRepo:
        def __init__(self, db):
            pass

        async def get(self, run_id):
            return SimpleNamespace(id=run_id, snapshot=None, last_sequence=0)

        async def finalize(self, **kwargs):
            captured.update(kwargs)
            return True

    class _FakeDb:
        async def commit(self):
            pass

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeDb()

        async def __aexit__(self, *args):
            return False

    # 服务内为函数内局部导入：桩源模块属性（调用时读取）
    import noesis.repositories.agent_run_repository as repo_mod
    import noesis.storage.postgres.manager as pg_mod

    monkeypatch.setattr(repo_mod, "AgentRunRepository", _FakeRepo)
    monkeypatch.setattr(
        pg_mod, "pg_manager",
        SimpleNamespace(get_async_session_context=lambda: _FakeCtx()),
    )

    await SubagentSessionService.mark_terminal(
        run_id="run-c",
        status=RunStatus.PARTIAL,
        error="任务已取消",
        finish_reason="cancelled",
    )
    assert captured["usage"] is None
