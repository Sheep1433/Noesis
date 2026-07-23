"""SuperAgent 用户记忆中间件回归。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from deepagents.backends.protocol import FileDownloadResponse
from deepagents.middleware.memory import MemoryMiddleware

from agent.backends.paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_USER_FILE
from agent.middlewares.memory_prompt import NOESIS_MEMORY_SYSTEM_PROMPT
from agent.middlewares.memory_sync_middleware import MemorySyncMiddleware
from agent.profiles.super_agent import (
    _MEMORY_SOURCES,
    _build_memory_middleware,
    _build_task_worker_subagents,
)
from constants.code_enum import IntentEnum


def test_memory_prompt_contains_agent_memory_placeholder() -> None:
    assert "{agent_memory}" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "<agent_memory>" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "USER.md" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "AGENTS.md" in NOESIS_MEMORY_SYSTEM_PROMPT


def test_memory_sources_order_user_before_agents() -> None:
    assert _MEMORY_SOURCES == [AGENT_MEMORY_USER_FILE, AGENT_MEMORY_AGENTS_FILE]


def test_build_memory_middleware_stack() -> None:
    backend = MagicMock()
    stack = _build_memory_middleware(backend)
    assert len(stack) == 2
    assert isinstance(stack[0], MemoryMiddleware)
    assert isinstance(stack[1], MemorySyncMiddleware)
    assert stack[0].sources == _MEMORY_SOURCES


def test_task_worker_subagents_exclude_memory_middleware() -> None:
    backend = MagicMock()
    subs = _build_task_worker_subagents(backend, [], [], user_id="u1")
    assert len(subs) == 1
    middleware = subs[0]["middleware"]
    assert not any(isinstance(m, MemoryMiddleware) for m in middleware)


def test_memory_sync_middleware_reloads_from_disk() -> None:
    backend = MagicMock()
    backend.download_files.return_value = [
        FileDownloadResponse(
            path=AGENT_MEMORY_USER_FILE,
            content=b"profile",
            error=None,
        ),
        FileDownloadResponse(
            path=AGENT_MEMORY_AGENTS_FILE,
            content=b"v2",
            error=None,
        ),
    ]
    mw = MemorySyncMiddleware(backend=backend, sources=list(_MEMORY_SOURCES))
    state = {"memory_contents": {AGENT_MEMORY_AGENTS_FILE: "stale"}}
    result = mw.before_model(state, MagicMock())
    assert result is not None
    assert result["memory_contents"][AGENT_MEMORY_AGENTS_FILE] == "v2"
    backend.download_files.assert_called_once_with(list(_MEMORY_SOURCES))


def test_memory_sync_skips_when_no_memory_state() -> None:
    backend = MagicMock()
    mw = MemorySyncMiddleware(backend=backend, sources=list(_MEMORY_SOURCES))
    assert mw.before_model({}, MagicMock()) is None


def test_intent_enum_excludes_deep_research_qa() -> None:
    registered = {item.value[0] for item in IntentEnum}
    assert "SUPER_AGENT_QA" in registered
    assert "DEEP_RESEARCH_QA" not in registered
