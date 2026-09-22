"""会话历史检索工具测试（session-history-search）：fake service + 身份闭包。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from noesis.agents.tools.history_search_tool import (
    SearchHistoryInput,
    SearchSessionsInput,
    build_history_search_tools,
)
from noesis.repositories.history_search import (
    HistoryHit,
    SessionAccessDenied,
    SessionGroup,
    SessionSearchResult,
)


@asynccontextmanager
async def _fake_pg(db):
    yield db


def _tools():
    return build_history_search_tools(user_id="u1", session_id="current-s")


def _find(tools, name):
    return next(tool for tool in tools if tool.name == name)


@pytest.mark.asyncio
async def test_search_history_renders_hits_and_defaults_to_bound_session() -> None:
    captured = {}

    async def fake_search(db, **kwargs):
        captured.update(kwargs)
        return SessionSearchResult(
            hits=[
                HistoryHit(
                    sequence=3,
                    role="user",
                    created_at=123,
                    text="路径 /etc/app.yaml",
                    truncated=False,
                )
            ],
            mode="search",
            boundary_unknown=False,
            cutoff_seq=3,
        )

    with patch(
        "noesis.agents.tools.history_search_tool.search_session_history",
        side_effect=fake_search,
    ), patch(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        side_effect=lambda: _fake_pg(object()),
    ):
        raw = await _find(_tools(), "search_history").coroutine(
            query="/etc/app.yaml", limit=5
        )
    payload = json.loads(raw)
    # 默认会话 = 闭包绑定的当前会话；user_id 不可由参数指定
    assert captured["user_id"] == "u1"
    assert captured["session_id"] == "current-s"
    assert payload["session_id"] == "current-s"
    assert payload["mode"] == "search"
    assert payload["results"][0]["sequence"] == 3
    assert payload["results"][0]["text"] == "路径 /etc/app.yaml"

    schema_fields = set(SearchHistoryInput.model_fields)
    assert "user_id" not in schema_fields


@pytest.mark.asyncio
async def test_search_history_explicit_session_and_access_denied() -> None:
    async def fake_search(db, **kwargs):
        if kwargs.get("session_id") == "s-other":
            raise SessionAccessDenied("s-other")
        return SessionSearchResult([], "search", False, None)

    with patch(
        "noesis.agents.tools.history_search_tool.search_session_history",
        side_effect=fake_search,
    ), patch(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        side_effect=lambda: _fake_pg(object()),
    ):
        tools = _tools()
        ok = json.loads(
            await _find(tools, "search_history").coroutine(
                query="kw", session_id="s-mine"
            )
        )
        denied = json.loads(
            await _find(_tools(), "search_history").coroutine(
                query="kw", session_id="s-other"
            )
        )
    assert ok["session_id"] == "s-mine"
    # 归属不符：统一文案，不区分不存在/无权限
    assert denied["error"] == "会话不存在或无权限访问"


@pytest.mark.asyncio
async def test_search_history_error_paths() -> None:
    async def value_error(db, **kwargs):
        raise ValueError("检索形态需要非空 query（或改用 around_sequence 滚动形态）")

    async def boom(db, **kwargs):
        raise RuntimeError("db down")

    with patch(
        "noesis.agents.tools.history_search_tool.search_session_history",
        side_effect=value_error,
    ), patch(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        side_effect=lambda: _fake_pg(object()),
    ):
        payload = json.loads(
            await _find(_tools(), "search_history").coroutine(query="")
        )
    assert "error" in payload

    with patch(
        "noesis.agents.tools.history_search_tool.search_session_history",
        side_effect=boom,
    ), patch(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        side_effect=lambda: _fake_pg(object()),
    ):
        payload = json.loads(
            await _find(_tools(), "search_history").coroutine(query="kw")
        )
    assert payload["error"] == "历史检索暂不可用"


@pytest.mark.asyncio
async def test_search_sessions_renders_groups_with_lineage() -> None:
    captured = {}

    async def fake_sessions(db, **kwargs):
        captured.update(kwargs)
        return [
            SessionGroup(
                session_id="s-old",
                title="旧会话",
                kind="subagent",
                parent_id="s-parent",
                created_at=1,
                updated_at=2,
                matched_sequence=4,
                matched_role="user",
                fragment="讨论过 pg_trgm",
                truncated=False,
            )
        ]

    with patch(
        "noesis.agents.tools.history_search_tool.search_user_sessions",
        side_effect=fake_sessions,
    ), patch(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        side_effect=lambda: _fake_pg(object()),
    ):
        raw = await _find(_tools(), "search_sessions").coroutine(
            query="pg_trgm", limit=5
        )
    # 归属过滤闭包绑定；当前会话被排除
    assert captured["user_id"] == "u1"
    assert captured["exclude_session_id"] == "current-s"
    payload = json.loads(raw)
    group = payload["results"][0]
    assert group["session_id"] == "s-old"
    assert group["kind"] == "subagent"
    assert group["parent_id"] == "s-parent"
    assert group["matched"]["fragment"] == "讨论过 pg_trgm"
    assert "user_id" not in SearchSessionsInput.model_fields


def test_descriptions_carry_source_first_and_layer_split() -> None:
    history = _find(_tools(), "search_history")
    sessions = _find(_tools(), "search_sessions")
    for tool in (history, sessions):
        assert "不构成外部事实的证据" in tool.description
        assert "先查原来源" in tool.description
    # 原文层 vs 蒸馏层分工只在 search_history 描述中展开
    assert "search_memory" in history.description
    assert "search_history" in sessions.description


def test_search_history_description_on_demand():
    """按需检索原则（防回归为一律先查的诱导）。

    评测侧无需再钉共享契约：压缩评测的检索组直接挂载本工具本身
    （evals/compression/agent_path.py），一致性由构造保证。
    """
    from noesis.agents.tools.history_search_tool import SEARCH_HISTORY_DESCRIPTION
    assert "按需使用" in SEARCH_HISTORY_DESCRIPTION
    assert "仅当所需细节" in SEARCH_HISTORY_DESCRIPTION
    assert "不为已有答案多做检索" in SEARCH_HISTORY_DESCRIPTION
