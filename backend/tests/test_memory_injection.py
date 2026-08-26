"""记忆注入中间件、选条与 /memory/ backend 条目扩展测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import WriteResult

import noesis.config.user_data_paths as user_data_paths
from noesis.agents.backends.memory import UserMemoryBackend
from noesis.agents.middlewares.memory_entries_middleware import MemoryEntriesMiddleware
from noesis.agents.tools.memory_tools import build_memory_tools
from noesis.services.memory.selection import MemorySelectionService
from noesis.services.memory.store import MemoryStore


@pytest.fixture()
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "users"
    monkeypatch.setattr(user_data_paths, "_USERS_ROOT", root)
    return root


def _seed(users_root: Path, uid: str = "u1") -> list[str]:
    paths = []
    for label, body in (("文档格式", "一律表格"), ("包管理", "统一 pnpm")):
        entry = MemoryStore.upsert_entry(
            uid, memory_type="preference", label=label, body=body, sources=[]
        )
        paths.append(entry.rel_path)
    return paths


# ----- 中间件 -----


def _middleware(select, run_id="run-1", uid="u1"):
    return MemoryEntriesMiddleware(run_id=run_id, user_id=uid, select=select)


@pytest.mark.asyncio
async def test_middleware_freezes_within_same_run(users_root: Path) -> None:
    _seed(users_root)
    select = AsyncMock(return_value=["preference/abc.md"])
    mw = _middleware(select)
    state = {"messages": []}
    first = await mw.abefore_agent(state, None)
    second = await mw.abefore_agent({**state, **first}, None)
    assert first is not None and second is None
    select.assert_awaited_once()


@pytest.mark.asyncio
async def test_middleware_reselects_on_new_run_with_already_surfaced(
    users_root: Path,
) -> None:
    _seed(users_root)
    select = AsyncMock(side_effect=[["preference/a.md"], ["preference/b.md"]])
    mw = _middleware(select)
    state = {"messages": []}
    first = await mw.abefore_agent(state, None)
    assert first["memory_entries_paths"] == ["preference/a.md"]
    mw_run2 = _middleware(select, run_id="run-2")
    second = await mw_run2.abefore_agent({**state, **first}, None)
    # 第二次选条输入的 exclude 含第一轮已注入条目（alreadySurfaced）
    assert select.await_args_list[1].args[1] == frozenset({"preference/a.md"})
    assert second["memory_entries_paths"] == ["preference/b.md"]
    assert set(second["memory_entries_surfaced"]) == {"preference/a.md", "preference/b.md"}


@pytest.mark.asyncio
async def test_middleware_selection_failure_yields_zero_injection(
    users_root: Path,
) -> None:
    async def boom(_query, _exclude):
        raise RuntimeError("selection down")

    mw = _middleware(boom)
    update = await mw.abefore_agent({"messages": []}, None)
    assert update["memory_entries_text"] == ""
    assert update["memory_entries_paths"] == []


@pytest.mark.asyncio
async def test_middleware_renders_selected_bodies_with_header(users_root: Path) -> None:
    paths = _seed(users_root)

    async def select(_query, _exclude):
        return paths[:1]

    mw = _middleware(select, uid="u1")
    update = await mw.abefore_agent({"messages": []}, None)
    assert "历史经验记忆" in update["memory_entries_text"]
    assert "一律表格" in update["memory_entries_text"]


# ----- 选条 -----


@pytest.mark.asyncio
async def test_selection_full_volume_when_small(users_root: Path) -> None:
    paths = _seed(users_root)
    selected = await MemorySelectionService.select("u1", "任意问题")
    assert set(selected) == set(paths)


@pytest.mark.asyncio
async def test_selection_uses_llm_when_over_budget(
    users_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import noesis.services.memory.selection as selection_mod

    paths = _seed(users_root)
    from types import SimpleNamespace

    monkeypatch.setattr(
        selection_mod,
        "MemoryConfig",
        SimpleNamespace(inject_budget_tokens=1, selection_model=""),
    )

    async def fake_llm(*, query, entries, top_k):
        return [entries[0].rel_path]

    monkeypatch.setattr(
        MemorySelectionService, "_run_llm", staticmethod(fake_llm)
    )
    selected = await MemorySelectionService.select("u1", "文档", exclude={paths[1]})
    assert selected == [paths[0]]


# ----- /memory/ backend 条目扩展 -----


def _backend(users_root: Path, uid: str = "u1") -> UserMemoryBackend:
    from noesis.config.user_data_paths import (
        ensure_user_memory_files,
        get_user_agents_md_path,
        get_user_profile_md_path,
    )

    ensure_user_memory_files(uid)
    return UserMemoryBackend(
        agents_path=get_user_agents_md_path(uid),
        user_path=get_user_profile_md_path(uid),
        user_id=uid,
    )


def test_backend_entry_write_syncs_index(users_root: Path) -> None:
    backend = _backend(users_root)
    result = backend.write("/preference/new-entry.md", "# 新条目\n\n正文内容\n")
    assert isinstance(result, WriteResult) and result.error is None
    state = MemoryStore.read_index("u1")
    assert any(
        e.slug == "new-entry" and e.label == "新条目" for e in state.entries
    )
    read = backend.read("/preference/new-entry.md")
    assert read.error is None


def test_backend_memory_index_and_journal_are_read_only(users_root: Path) -> None:
    MemoryStore.append_journal("u1", session_id="sess-1", text="日志")
    backend = _backend(users_root)
    assert backend.write("/MEMORY.md", "x").error == "permission_denied"
    assert backend.write("/journal/2026-08-26.md", "x").error == "permission_denied"
    assert backend.read("/MEMORY.md").error is None


def test_backend_rejects_paths_outside_memory_tree(users_root: Path) -> None:
    backend = _backend(users_root)
    assert backend.read("/channels.json").error == "file_not_found"
    with pytest.raises(ValueError, match="Path traversal"):
        backend.write("/../secret.md", "x")


# ----- search_memory 工具 -----


@pytest.mark.asyncio
async def test_search_memory_tool_returns_entry_content(users_root: Path) -> None:
    _seed(users_root)
    tools = build_memory_tools(user_id="u1")
    assert tools[0].name == "search_memory"
    import json

    payload = json.loads(await tools[0].ainvoke({"query": "pnpm"}))
    assert payload["results"]
    assert payload["results"][0]["memory_type"] == "preference"
    assert "统一 pnpm" in payload["results"][0]["content"]


@pytest.mark.asyncio
async def test_search_memory_tool_rejects_invalid_type(users_root: Path) -> None:
    _seed(users_root)
    tools = build_memory_tools(user_id="u1")
    import json

    payload = json.loads(
        await tools[0].ainvoke({"query": "x", "memory_type": "workflow"})
    )
    assert "error" in payload
