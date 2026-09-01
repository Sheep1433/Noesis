"""Agentic 召回：search_memory 工具（回写 + stale）与 /memory/ backend 条目扩展。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import WriteResult

import noesis.config.user_data_paths as user_data_paths
from noesis.agents.backends.memory import UserMemoryBackend
from noesis.agents.prompts.memory import NOESIS_MEMORY_SYSTEM_PROMPT
from noesis.agents.tools.memory_tools import build_memory_tools
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


# ----- search_memory：召回清单回写（root run） -----


class _RecallDB:
    """只回放 memory_context 读-合并-写所需的最小 DB 形态。"""

    def __init__(self, existing_ctx: dict | None) -> None:
        self.existing_ctx = existing_ctx
        self.updates: list = []
        self.commits = 0

    async def execute(self, stmt):
        if getattr(stmt, "__visit_name__", "") == "update":
            self.updates.append(stmt)
            return None
        return SimpleNamespace(scalar_one_or_none=lambda: self.existing_ctx)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


@pytest.mark.asyncio
async def test_search_memory_merges_recall_list_into_memory_context(
    users_root: Path,
) -> None:
    paths = _seed(users_root)
    db = _RecallDB(existing_ctx={"entries": [paths[0]]})
    tools = build_memory_tools(user_id="u1", run_id="run-1", db=db)
    payload = json.loads(await tools[0].ainvoke({"query": "pnpm"}))
    assert payload["results"] and payload["results"][0]["rel_path"] == paths[1]
    assert db.commits == 1
    assert len(db.updates) == 1
    values = db.updates[0].compile().params
    # 读-合并-写：既有清单保留，新命中去重追加
    assert values["memory_context"]["entries"] == [paths[0], paths[1]]


@pytest.mark.asyncio
async def test_search_memory_without_run_id_writes_nothing(users_root: Path) -> None:
    """subagent 只读：不传 run_id/db → 检索结果照常、不产生任何回写。"""
    _seed(users_root)
    db = _RecallDB(existing_ctx=None)
    tools = build_memory_tools(user_id="u1")  # 只读装配
    payload = json.loads(await tools[0].ainvoke({"query": "表格"}))
    assert payload["results"]
    assert db.updates == [] and db.commits == 0


@pytest.mark.asyncio
async def test_search_memory_writeback_failure_does_not_break_results(
    users_root: Path,
) -> None:
    _seed(users_root)

    class _BrokenDB(_RecallDB):
        async def execute(self, stmt):
            raise RuntimeError("db down")

    tools = build_memory_tools(user_id="u1", run_id="run-1", db=_BrokenDB(None))
    payload = json.loads(await tools[0].ainvoke({"query": "pnpm"}))
    assert payload["results"]  # 回写失败不阻断检索结果


@pytest.mark.asyncio
async def test_search_memory_attaches_stale_warning(users_root: Path) -> None:
    paths = _seed(users_root)
    entry_file = users_root / "u1" / "memory" / paths[0]
    ancient = time.time() - 365 * 86_400
    os.utime(entry_file, (ancient, ancient))
    tools = build_memory_tools(user_id="u1", run_id="run-1", db=_RecallDB(None))
    payload = json.loads(await tools[0].ainvoke({"query": "表格 pnpm"}))
    stale = {r["rel_path"]: r["stale_warning"] for r in payload["results"]}
    assert "天前" in stale[paths[0]]
    assert stale[paths[1]] == ""  # 新条目不附警告


@pytest.mark.asyncio
async def test_search_memory_failure_returns_error_not_raise(
    users_root: Path,
) -> None:
    _seed(users_root)
    tools = build_memory_tools(user_id="u1", run_id="run-1", db=_RecallDB(None))
    with patch.object(
        MemoryStore, "search", side_effect=RuntimeError("fs down")
    ):
        payload = json.loads(await tools[0].ainvoke({"query": "任意"}))
    assert "error" in payload


@pytest.mark.asyncio
async def test_search_memory_tool_rejects_invalid_type(users_root: Path) -> None:
    _seed(users_root)
    tools = build_memory_tools(user_id="u1")
    payload = json.loads(
        await tools[0].ainvoke({"query": "x", "memory_type": "workflow"})
    )
    assert "error" in payload


# ----- 应召回场景行为断言（含记忆线索的对话 → Agent 调用 search_memory） -----


@pytest.mark.asyncio
async def test_recall_scenario_agent_calls_search_memory(users_root: Path) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    from noesis.factory import create_noesis_agent

    paths = _seed(users_root)
    db = _RecallDB(existing_ctx=None)

    class _ScriptedLLM(GenericFakeChatModel):
        """脚本回放模型：支持 bind_tools（工具绑定对回放无意义）。"""

        def bind_tools(self, tools, **kwargs):  # noqa: ARG002
            return self

    scripted = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_memory",
                        "args": {"query": "pnpm"},
                        "id": "call-recall-1",
                    }
                ],
            ),
            AIMessage(content="用户此前已定包管理器统一使用 pnpm。"),
        ]
    )
    fake_llm = _ScriptedLLM(messages=scripted)
    cfg = SimpleNamespace(
        summarization_enabled=False, tool_output_max_chars=24_000, max_retries=6
    )
    with (
        patch("noesis.factory.ModelConfig", cfg),
        patch("noesis.factory.get_llm", return_value=fake_llm),
    ):
        agent = create_noesis_agent(
            profile="SUPER_AGENT_QA",
            tools=build_memory_tools(user_id="u1", run_id="run-recall", db=db),
            system_prompt="You are a test assistant.",
            checkpointer=None,
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="我之前对包管理器有什么偏好？")]}
        )
    # 工具真实执行：检索结果以 ToolMessage 进入对话历史（随会话持久化）
    tool_messages = [
        m for m in result["messages"] if getattr(m, "type", "") == "tool"
    ]
    assert tool_messages and "统一 pnpm" in str(tool_messages[0].content)
    # root run 召回清单回写 run.memory_context
    assert db.updates and db.updates[0].compile().params["memory_context"][
        "entries"
    ] == [paths[1]]


# ----- 召回纪律与写入模板（prompt） -----


def test_memory_prompt_contains_recall_discipline() -> None:
    assert "召回纪律" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "先检索再产出" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "search_memory" in NOESIS_MEMORY_SYSTEM_PROMPT
    assert "description（「是什么；何时调用」）" in NOESIS_MEMORY_SYSTEM_PROMPT


def test_memory_prompt_contains_frontmatter_template() -> None:
    prompt = NOESIS_MEMORY_SYSTEM_PROMPT
    assert "type: preference" in prompt
    assert "description: 一句话结论；何时调用" in prompt
    assert "sources:" in prompt
    assert "不新增字段" in prompt
    assert "带时效性的内容只进 goal" in prompt


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


def test_backend_frontmatter_write_projects_description(users_root: Path) -> None:
    """Agent 按 frontmatter 模板直写：索引行从结构化字段取得投影。"""
    backend = _backend(users_root)
    content = (
        "---\n"
        "type: preference\n"
        "label: 输出语言\n"
        "description: 偏好简体中文回复；所有产出场景调用\n"
        "created: 2026-09-01\n"
        "updated: 2026-09-01\n"
        "sources:\n  - 会话 abcd1234 · 2026-09-01\n"
        "---\n\n# 输出语言\n\n始终用简体中文。\n"
    )
    assert backend.write("/preference/output-language.md", content).error is None
    state = MemoryStore.read_index("u1")
    entry = next(e for e in state.entries if e.slug == "output-language")
    assert entry.label == "输出语言"
    assert entry.description == "偏好简体中文回复；所有产出场景调用"


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


# ----- 侧边栏记忆树（SessionContextService） -----


@pytest.mark.asyncio
async def test_session_context_includes_memory_tree(users_root: Path) -> None:
    from noesis.services.session_context_service import SessionContextService

    _seed(users_root)
    MemoryStore.append_journal("u1", session_id="sess-1", text="日志")
    with patch("noesis.services.session_context_service.ChatService.get_session_by_id", AsyncMock(return_value=MagicMock())):
        payload = await SessionContextService.get_context(
            session_id="sess-1", user_id="u1", db=MagicMock()
        )
    keys = [node.key for node in payload.tree[0].children or []]
    assert "memory" in keys
    memory = next(n for n in payload.tree[0].children if n.key == "memory")
    child_keys = [c.key for c in memory.children or []]
    assert "memory/MEMORY.md" in child_keys
    assert "memory/preference" in child_keys
    assert "memory/journal" in child_keys


@pytest.mark.asyncio
async def test_memory_tree_hidden_when_empty(users_root: Path) -> None:
    from noesis.services.session_context_service import SessionContextService

    with patch("noesis.services.session_context_service.ChatService.get_session_by_id", AsyncMock(return_value=MagicMock())):
        payload = await SessionContextService.get_context(
            session_id="sess-1", user_id="u1", db=MagicMock()
        )
    keys = [node.key for node in payload.tree[0].children or []]
    assert "memory" not in keys


@pytest.mark.asyncio
async def test_sidebar_memory_entry_edit_syncs_index(users_root: Path) -> None:
    from noesis.services.session_context_service import SessionContextService

    entry = MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="文档格式", body="一律表格", sources=[]
    )
    with patch("noesis.services.session_context_service.ChatService.get_session_by_id", AsyncMock(return_value=MagicMock())):
        rel, content = await SessionContextService.write_workspace_file(
            session_id="sess-1",
            user_id="u1",
            rel_path=f"memory/{entry.rel_path}",
            content="# 文档格式\n\n一律表格、带脚注\n",
            db=MagicMock(),
        )
    assert rel == f"memory/{entry.rel_path}"
    state = MemoryStore.read_index("u1")
    assert any("带脚注" in e.description for e in state.entries)


@pytest.mark.asyncio
async def test_sidebar_memory_index_and_journal_read_only(users_root: Path) -> None:
    from noesis.errors.exceptions import ServiceException
    from noesis.services.session_context_service import SessionContextService

    MemoryStore.append_journal("u1", session_id="sess-1", text="日志")
    with patch(
        "noesis.services.session_context_service.ChatService.get_session_by_id",
        AsyncMock(return_value=MagicMock()),
    ):
        for path in ("memory/MEMORY.md", "memory/journal/2026-08-26.md"):
            with pytest.raises(ServiceException) as exc_info:
                await SessionContextService.write_workspace_file(
                    session_id="sess-1", user_id="u1", rel_path=path,
                    content="x", db=MagicMock(),
                )
            assert "只读" in str(exc_info.value.message)
