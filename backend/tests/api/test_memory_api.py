"""记忆层接口用例（integration）：真实 HTTP 验证 md 文件记忆层全部 API。

前置：后端已启动（uv run app.py）；demo 账号 test/123456（与 conftest 登录一致）。
运行：``uv run pytest tests/api/test_memory_api.py -m integration -v``

覆盖面：
- 记忆开关（GET/PUT /api/user/memory/settings）
- 记忆树（/api/user/memory/tree）
- 条目生命周期（读/编辑/删除 + 索引同步）
- journal 读取（/api/user/memory/journal/{day}）
- 索引重建（POST /api/user/memory/index/rebuild）
- 上下文预览注入 MEMORY.md 索引（/api/user/context/preview）
- 侧边栏会话上下文含 memory 树（/api/chat/sessions/{sid}/context）
- 显式记忆文件回归（GET/PUT /api/user/memory/AGENTS.md）
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from noesis.config.user_data_paths import _USERS_ROOT  # noqa: F401  (确保模块可导入)
from noesis.services.memory.store import MemoryStore

pytestmark = [pytest.mark.integration]

TEST_LABEL = f"接口验证{uuid.uuid4().hex[:6]}"


def _current_user_id() -> str:
    """登录接口不回传 user_id；按登录用户名从 DB 查。

    种子身份必须与 conftest 的登录账号一致（test）——文件层种子写的
    是该用户的 memory 目录，API 侧按会话用户过滤，两边错位则种子永远不可见。
    """
    import asyncio
    import os

    from sqlalchemy import text

    from noesis.storage.postgres.manager import pg_manager

    username = os.environ.get("NOESIS_TEST_USER", "test")

    async def run() -> str:
        async with pg_manager.get_async_session_context() as db:
            return str(
                (
                    await db.execute(
                        text("SELECT id FROM t_user WHERE username = :u"),
                        {"u": username},
                    )
                ).scalar()
            )

    return asyncio.run(run())


@pytest.fixture(scope="module")
def user_id() -> str:
    return _current_user_id()


@pytest.fixture()
def seeded_entry(user_id: str):
    """文件层直接造一条测试条目（API 无创建端点——创建走引擎抽取/Agent 写入）。"""
    entry = MemoryStore.upsert_entry(
        user_id,
        memory_type="preference",
        label=TEST_LABEL,
        body="接口验证用临时条目，测试后删除",
        sources=["接口用例"],
    )
    yield entry
    MemoryStore.remove_entry(user_id, entry.memory_type, entry.slug)


# ----- 记忆开关 -----


def test_memory_settings_toggle_roundtrip(auth_client) -> None:
    resp = auth_client.get("/api/user/memory/settings")
    resp.raise_for_status()
    original = resp.json()["data"]["enabled"]

    resp = auth_client.put("/api/user/memory/settings", json={"enabled": not original})
    resp.raise_for_status()
    assert resp.json()["data"]["enabled"] is (not original)

    resp = auth_client.get("/api/user/memory/settings")
    assert resp.json()["data"]["enabled"] is (not original)

    # 还原
    resp = auth_client.put("/api/user/memory/settings", json={"enabled": original})
    resp.raise_for_status()
    assert resp.json()["data"]["enabled"] is original


def test_memory_settings_requires_csrf(auth_client) -> None:
    client = auth_client
    saved = client.headers.pop("X-CSRF-Token", None)
    try:
        resp = client.put("/api/user/memory/settings", json={"enabled": True})
        assert resp.status_code in (403, 401), "无 CSRF 头的写操作应被拒绝"
    finally:
        if saved:
            client.headers["X-CSRF-Token"] = saved


# ----- 记忆树 -----


def test_memory_tree_lists_entries_and_journal(
    auth_client, user_id: str, seeded_entry
) -> None:
    resp = auth_client.get("/api/user/memory/tree")
    resp.raise_for_status()
    data = resp.json()["data"]
    assert any(
        e["label"] == TEST_LABEL for e in data["entries"]
    ), "种子条目应出现在树中"
    entry = next(e for e in data["entries"] if e["label"] == TEST_LABEL)
    assert entry["memory_type"] == "preference"
    assert entry["type_label"] == "偏好"
    assert entry["rel_path"].startswith("preference/")
    assert isinstance(data["journal_days"], list)


# ----- 条目生命周期 -----


def test_entry_read_update_delete_with_index_sync(
    auth_client, user_id: str, seeded_entry
) -> None:
    # 读
    resp = auth_client.get(
        f"/api/user/memory/entry/{seeded_entry.memory_type}/{seeded_entry.slug}"
    )
    resp.raise_for_status()
    content = resp.json()["data"]["content"]
    assert TEST_LABEL in content

    # 改（用户最高权限编辑；索引行应同步）
    updated = content.replace(
        "接口验证用临时条目，测试后删除", "接口验证用临时条目，已编辑"
    )
    resp = auth_client.put(
        f"/api/user/memory/entry/{seeded_entry.memory_type}/{seeded_entry.slug}",
        json={"content": updated},
    )
    resp.raise_for_status()
    state = MemoryStore.read_index(user_id)
    indexed = next(
        (e for e in state.entries if e.slug == seeded_entry.slug), None
    )
    assert indexed is not None and "已编辑" in indexed.description, "索引行应随编辑同步"

    # 删
    resp = auth_client.delete(
        f"/api/user/memory/entry/{seeded_entry.memory_type}/{seeded_entry.slug}"
    )
    resp.raise_for_status()
    resp = auth_client.get(
        f"/api/user/memory/entry/{seeded_entry.memory_type}/{seeded_entry.slug}"
    )
    assert resp.status_code == 404


def test_entry_rejects_invalid_type(auth_client) -> None:
    resp = auth_client.get("/api/user/memory/entry/workflow/whatever")
    assert resp.status_code == 400


# ----- journal / 索引重建 -----


def test_journal_read(auth_client, user_id: str) -> None:
    MemoryStore.append_journal(
        user_id, session_id=f"api-{uuid.uuid4().hex[:8]}", text="接口用例 journal 验证"
    )
    try:
        resp = auth_client.get(f"/api/user/memory/journal/{date.today().isoformat()}")
        resp.raise_for_status()
        assert "接口用例 journal 验证" in resp.json()["data"]["content"]
    finally:
        # 清理本用例追加的段落（journal 只追加，测试造的段落直接截断删除）
        path = MemoryStore.journal_path(user_id)
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                next(
                    block
                    for block in text.split("\n\n## ")
                    if "接口用例 journal 验证" in block
                ).join(["## ", ""]),
                "",
            ),
            encoding="utf-8",
        )


def test_index_rebuild(auth_client, user_id: str, seeded_entry) -> None:
    resp = auth_client.post("/api/user/memory/index/rebuild")
    resp.raise_for_status()
    state = MemoryStore.read_index(user_id)
    assert any(e.slug == seeded_entry.slug for e in state.entries), "重建后种子条目仍在"


# ----- 注入链路 -----


def test_context_preview_includes_memory_index(auth_client) -> None:
    resp = auth_client.get(
        "/api/user/context/preview", params={"profile": "super_agent"}
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    sources = data["sources"]
    memory_source = next((s for s in sources if s.get("id") == "memory-index"), None)
    assert memory_source is not None, "预览应包含 MEMORY.md 索引源"
    compiled = data["compiled_content"]
    # compiled = 模型真实所见：XML 包裹 + 文件路径头 + guidelines，
    # 与自拼标签预览（## 用户画像）区分
    assert "<agent_memory>" in compiled and "</agent_memory>" in compiled
    assert "/memory/USER.md" in compiled and "/memory/AGENTS.md" in compiled
    assert "<memory_guidelines>" in compiled
    assert "<!--" not in compiled, "HTML 注释应被剥离（与真实注入一致）"
    assert "## 用户画像" not in compiled, "预览不得使用自拼标签替代真实形态"


def test_session_context_contains_memory_tree(
    auth_client, create_session, user_id: str, seeded_entry
) -> None:
    session_id = create_session(title="记忆接口验证")
    resp = auth_client.get(f"/api/chat/sessions/{session_id}/context")
    resp.raise_for_status()
    tree = resp.json()["data"]["tree"]

    def walk(nodes):
        for node in nodes or []:
            yield node
            yield from walk(node.get("children"))

    keys = [n["key"] for n in walk(tree)]
    assert "memory" in keys, "侧边栏上下文应含 memory 节点"
    assert any(k.startswith("memory/preference/") for k in keys), "应列出条目文件"


# ----- 显式记忆回归（既有能力不受影响） -----


def test_explicit_memory_file_roundtrip(auth_client) -> None:
    resp = auth_client.get("/api/user/memory/AGENTS.md")
    resp.raise_for_status()
    original = resp.json()["data"]["content"]

    marker = f"<!-- api-test-{uuid.uuid4().hex[:8]} -->"
    resp = auth_client.put(
        "/api/user/memory/AGENTS.md", json={"content": original + marker}
    )
    resp.raise_for_status()
    try:
        resp = auth_client.get("/api/user/memory/AGENTS.md")
        assert marker in resp.json()["data"]["content"]
    finally:
        auth_client.put("/api/user/memory/AGENTS.md", json={"content": original})
