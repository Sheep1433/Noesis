"""SuperAgent 用户记忆中间件回归。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, NonCallableMagicMock, patch

import pytest
from deepagents.backends.protocol import FileDownloadResponse
from deepagents.middleware.memory import MemoryMiddleware

from noesis.agents.backends.paths import (
    AGENT_MEMORY_AGENTS_FILE,
    AGENT_MEMORY_INDEX_FILE,
    AGENT_MEMORY_USER_FILE,
)
from noesis.agents.middlewares.refreshing_memory_middleware import RefreshingMemoryMiddleware
from noesis.agents.prompts.memory import NOESIS_MEMORY_SYSTEM_PROMPT
from noesis.agents.super_agent import (
    _MEMORY_SOURCES,
    _compile_task_worker,
)
from noesis.config.code_enum import IntentEnum


def test_memory_prompt_contains_agent_memory_placeholder() -> None:
    assert "{agent_memory}" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "<agent_memory>" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "USER.md" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "AGENTS.md" in NOESIS_MEMORY_SYSTEM_PROMPT


def test_memory_sources_order_user_before_agents() -> None:
    assert _MEMORY_SOURCES == [
        AGENT_MEMORY_USER_FILE,
        AGENT_MEMORY_AGENTS_FILE,
        AGENT_MEMORY_INDEX_FILE,
    ]


def test_task_worker_excludes_memory_middleware() -> None:
    backend = MagicMock()
    worker = _compile_task_worker(backend, [], [], user_id="u1", model_id=None)
    # worker 是编译好的 runnable；从其 nodes 检查中间件装配结果不可达，
    # 改为验证 SUBAGENT 栈构建不含 MemoryMiddleware（memory 只挂主 Agent）
    from noesis.factory import build_noesis_middleware
    with patch("noesis.factory.ModelConfig", MagicMock(summarization_enabled=False, tool_output_max_chars=24_000, max_retries=6)):
        middleware = build_noesis_middleware(
            profile="SUBAGENT", model=MagicMock(), backend=backend,
            memory=(), skills=(),
        )
    assert not any(isinstance(m, MemoryMiddleware) for m in middleware)
    assert worker is not None


def test_turn_memory_reloads_once_for_each_agent_invocation() -> None:
    backend = NonCallableMagicMock()
    backend.download_files.side_effect = [
        [
            FileDownloadResponse(path=AGENT_MEMORY_USER_FILE, content=b"profile-v1", error=None),
            FileDownloadResponse(path=AGENT_MEMORY_AGENTS_FILE, content=b"rules-v1", error=None),
            FileDownloadResponse(path=AGENT_MEMORY_INDEX_FILE, content=b"index-v1", error=None),
        ],
        [
            FileDownloadResponse(path=AGENT_MEMORY_USER_FILE, content=b"profile-v2", error=None),
            FileDownloadResponse(path=AGENT_MEMORY_AGENTS_FILE, content=b"rules-v2", error=None),
            FileDownloadResponse(path=AGENT_MEMORY_INDEX_FILE, content=b"index-v2", error=None),
        ],
    ]
    mw = RefreshingMemoryMiddleware(backend=backend, sources=list(_MEMORY_SOURCES))
    state = {"memory_contents": {AGENT_MEMORY_AGENTS_FILE: "stale"}}
    first = mw.before_agent(state, MagicMock(), {})
    second = mw.before_agent({**state, **(first or {})}, MagicMock(), {})

    assert first is not None
    assert second is not None
    assert first["memory_contents"][AGENT_MEMORY_AGENTS_FILE] == "rules-v1"
    assert second["memory_contents"][AGENT_MEMORY_AGENTS_FILE] == "rules-v2"
    assert backend.download_files.call_count == 2
    backend.download_files.assert_called_with(list(_MEMORY_SOURCES))


def test_turn_memory_has_no_per_model_reload_hook() -> None:
    assert "before_model" not in RefreshingMemoryMiddleware.__dict__
    assert "abefore_model" not in RefreshingMemoryMiddleware.__dict__


@pytest.mark.asyncio
async def test_turn_memory_async_path_ignores_checkpoint_cache() -> None:
    backend = NonCallableMagicMock()
    backend.adownload_files = AsyncMock(
        return_value=[
            FileDownloadResponse(path=AGENT_MEMORY_USER_FILE, content=b"profile", error=None),
            FileDownloadResponse(path=AGENT_MEMORY_AGENTS_FILE, content=b"fresh", error=None),
            FileDownloadResponse(path=AGENT_MEMORY_INDEX_FILE, content=b"index", error=None),
        ]
    )
    middleware = RefreshingMemoryMiddleware(backend=backend, sources=list(_MEMORY_SOURCES))

    result = await middleware.abefore_agent(
        {"memory_contents": {AGENT_MEMORY_AGENTS_FILE: "stale"}},
        MagicMock(),
        {},
    )

    assert result is not None
    assert result["memory_contents"][AGENT_MEMORY_AGENTS_FILE] == "fresh"
    backend.adownload_files.assert_awaited_once_with(list(_MEMORY_SOURCES))


def test_intent_enum_excludes_deep_research_qa() -> None:
    registered = {item.value[0] for item in IntentEnum}
    assert "SUPER_AGENT_QA" in registered
    assert "DEEP_RESEARCH_QA" not in registered
