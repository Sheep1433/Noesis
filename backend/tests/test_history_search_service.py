"""会话历史检索服务测试（session-history-search）。

三层：
1. 纯逻辑（渲染/截断/预算）——无依赖，恒跑；
2. fake session 语句结构断言——过滤条件与排序形态；
3. 真实 PG 集成档——pg_trgm 命中、压缩边界过滤、跨会话隔离、边界写入
   （``NOESIS_LIVE_POSTGRES_TEST=1 uv run pytest -m integration``，需先
   ``alembic upgrade head`` 使 compaction_cutoff_seq 列与 GIN 索引就位）。
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from noesis.services.history_search import (
    SessionAccessDenied,
    _apply_total_budget,
    _escape_like,
    excerpt_around,
    record_compaction_boundary,
    render_content_text,
    search_session_history,
    search_user_sessions,
)


# ---------- 纯逻辑 ----------


def test_render_content_text_extracts_text_and_tool_parts() -> None:
    content = {
        "parts": [
            {"type": "reasoning", "content": "思考中"},
            {"type": "text", "content": "看下这个报错"},
            {
                "type": "tool",
                "name": "execute",
                "input": {"command": "pytest -q"},
                "output": "3 passed",
            },
            {"type": "retrieval", "query": "q", "results": []},
        ]
    }
    text = render_content_text(content)
    assert "看下这个报错" in text
    assert "[tool:execute]" in text and "pytest -q" in text and "3 passed" in text
    # reasoning/retrieval 不进渲染，原始 JSON 键不出现
    assert "思考中" not in text
    assert '"type"' not in text


def test_render_content_text_legacy_shapes() -> None:
    assert render_content_text(None) == ""
    assert render_content_text("裸文本") == "裸文本"
    assert "legacy" in render_content_text({"no_parts": "legacy"})


def test_excerpt_around_short_text_kept_intact() -> None:
    text, truncated = excerpt_around("短文本", "短", 2000)
    assert text == "短文本" and truncated is False


def test_excerpt_around_long_text_centers_on_keyword() -> None:
    text = "x" * 5000 + "NEEDLE" + "y" * 5000
    excerpt, truncated = excerpt_around(text, "NEEDLE", 100)
    assert truncated is True
    assert "NEEDLE" in excerpt
    assert len(excerpt) == 100


def test_excerpt_around_long_text_without_keyword_takes_head() -> None:
    text = "z" * 3000
    excerpt, truncated = excerpt_around(text, "missing", 100)
    assert truncated is True
    assert excerpt == "z" * 100


def test_apply_total_budget_cuts_overflow_hits() -> None:
    from noesis.services.history_search import HistoryHit

    hits = [
        HistoryHit(1, "user", 0, "a" * 60, False),
        HistoryHit(2, "user", 0, "b" * 60, False),
        HistoryHit(3, "user", 0, "c" * 60, False),
    ]
    kept = _apply_total_budget(hits, 100)
    assert [hit.sequence for hit in kept] == [1]
    # 首条超预算也至少保留一条（宁返回一条不空手）
    assert len(_apply_total_budget(hits, 10)) == 1


def test_escape_like_neutralizes_pattern_characters() -> None:
    assert _escape_like("a%b_c\\d") == "a\\%b\\_c\\\\d"


# ---------- fake session 语句结构 ----------


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    """按调用次序返回预设结果，并捕获每条语句。"""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self._results.pop(0)

    async def commit(self):
        pass


def _session_row(cutoff=None, user_id="u1"):
    return SimpleNamespace(compaction_cutoff_seq=cutoff, user_id=user_id)


def _msg_row(seq, role, content, created_at=1):
    return SimpleNamespace(
        message_sequence=seq, role=role, created_at=created_at, content=content
    )


@pytest.mark.asyncio
async def test_search_mode_filters_by_rendered_text_and_sorts_by_sequence() -> None:
    db = _FakeDb(
        [
            _ScalarResult(_session_row(cutoff=None)),
            _RowsResult(
                [
                    _msg_row(7, "assistant", {"parts": [{"type": "text", "content": "路径是 /var/log/app.log"}]}),
                    # JSON 结构键命中但渲染文本不含关键词 → 淘汰
                    _msg_row(8, "assistant", {"parts": [{"type": "text", "content": "无关内容"}]}),
                    _msg_row(3, "user", {"parts": [{"type": "text", "content": "报错 /var/log/app.log 权限"}]}),
                ]
            ),
        ]
    )
    result = await search_session_history(
        db, user_id="u1", session_id="s1", query="/var/log/app.log", limit=5
    )
    assert result.mode == "search"
    assert [hit.sequence for hit in result.hits] == [3, 7]
    assert all("/var/log/app.log" in hit.text for hit in result.hits)


@pytest.mark.asyncio
async def test_before_compaction_with_cutoff_filters_sequence() -> None:
    db = _FakeDb(
        [
            _ScalarResult(_session_row(cutoff=10)),
            _RowsResult([]),
        ]
    )
    await search_session_history(
        db, user_id="u1", session_id="s1", query="kw", before_compaction=True
    )
    compiled = str(
        db.statements[1].compile(compile_kwargs={"literal_binds": True})
    )
    assert "message_sequence <=" in compiled
    assert "10" in compiled


@pytest.mark.asyncio
async def test_before_compaction_without_cutoff_degrades_to_full_history() -> None:
    db = _FakeDb(
        [
            _ScalarResult(_session_row(cutoff=None)),
            _RowsResult(
                [_msg_row(1, "user", {"parts": [{"type": "text", "content": "kw 命中"}]})]
            ),
        ]
    )
    result = await search_session_history(
        db, user_id="u1", session_id="s1", query="kw", before_compaction=True
    )
    # 边界缺失：不拒绝检索，降级为全历史并标注
    assert result.boundary_unknown is True
    assert result.hits and result.hits[0].sequence == 1
    compiled = str(
        db.statements[1].compile(compile_kwargs={"literal_binds": True})
    )
    assert "message_sequence <=" not in compiled


@pytest.mark.asyncio
async def test_scroll_mode_returns_window_around_sequence() -> None:
    # fake 不执行 SQL：返回集模拟 WHERE sequence BETWEEN 3 AND 7 的结果，
    # 窗口边界本身由下方 compiled 语句断言覆盖
    rows = [
        _msg_row(seq, "user" if seq % 2 else "assistant", {"parts": [{"type": "text", "content": f"m{seq}"}]})
        for seq in range(3, 8)
    ]
    db = _FakeDb(
        [
            _ScalarResult(_session_row(cutoff=None)),
            _RowsResult(rows),
        ]
    )
    result = await search_session_history(
        db, user_id="u1", session_id="s1", around_sequence=5, window=2
    )
    assert result.mode == "scroll"
    assert [hit.sequence for hit in result.hits] == [3, 4, 5, 6, 7]
    compiled = str(
        db.statements[1].compile(compile_kwargs={"literal_binds": True})
    )
    assert ">=" in compiled and "<=" in compiled


@pytest.mark.asyncio
async def test_session_not_owned_raises_access_denied() -> None:
    db = _FakeDb([_ScalarResult(None)])
    with pytest.raises(SessionAccessDenied):
        await search_session_history(
            db, user_id="u1", session_id="s-other", query="kw"
        )


@pytest.mark.asyncio
async def test_search_requires_query_in_search_mode() -> None:
    db = _FakeDb([_ScalarResult(_session_row())])
    with pytest.raises(ValueError):
        await search_session_history(db, user_id="u1", session_id="s1", query="  ")


@pytest.mark.asyncio
async def test_search_user_sessions_groups_and_excludes_current() -> None:
    row = SimpleNamespace(
        session_id="s-old",
        message_sequence=4,
        role="user",
        content={"parts": [{"type": "text", "content": "讨论过 pg_trgm"}]},
        title="旧会话",
        kind="root",
        parent_id=None,
        created_at=1,
        updated_at=2,
    )
    db = _FakeDb([_RowsResult([row, row])])
    groups = await search_user_sessions(
        db, user_id="u1", exclude_session_id="s-current", query="pg_trgm", limit=5
    )
    assert len(groups) == 1
    assert groups[0].session_id == "s-old"
    assert groups[0].matched_sequence == 4
    assert "pg_trgm" in groups[0].fragment
    compiled = str(
        db.statements[0].compile(compile_kwargs={"literal_binds": True})
    )
    # 归属过滤 + 排除当前会话都进 SQL
    assert "s-current" in compiled
    assert "u1" in compiled


@pytest.mark.asyncio
async def test_search_user_sessions_requires_query() -> None:
    db = _FakeDb([])
    with pytest.raises(ValueError):
        await search_user_sessions(
            db, user_id="u1", exclude_session_id="s1", query=""
        )


# ---------- 真实 PG 集成档 ----------


def _live_pg_enabled() -> bool:
    return os.getenv("NOESIS_LIVE_POSTGRES_TEST") == "1"


pytestmark_integration = pytest.mark.skipif(
    not _live_pg_enabled(), reason="需 NOESIS_LIVE_POSTGRES_TEST=1 与本地 PostgreSQL"
)


@pytest.mark.integration
@pytestmark_integration
@pytest.mark.asyncio
async def test_live_search_and_boundary_roundtrip() -> None:
    from noesis.storage.postgres.manager import pg_manager
    from noesis.storage.postgres.models.chat import (
        TAgentRun,
        TChatMessage,
        TChatSession,
    )
    from sqlalchemy import delete, insert, select

    user_id = str(uuid.uuid4())
    prefix = uuid.uuid4().hex[:8]
    async with pg_manager.get_async_session_context() as db:
        session_id = f"hist-{prefix}"
        db.add(
            TChatSession(
                id=session_id,
                user_id=user_id,
                title="历史检索集成",
                next_message_sequence=1,
            )
        )
        other_id = f"hist-{prefix}-other"
        db.add(
            TChatSession(
                id=other_id,
                user_id=str(uuid.uuid4()),
                title="他人会话",
                next_message_sequence=1,
            )
        )
        await db.commit()

        def _content(text):
            return {"parts": [{"type": "text", "content": text}]}

        rows = [
            TChatMessage(
                id=f"{prefix}-{seq}",
                session_id=session_id,
                user_id=user_id,
                role="user" if seq % 2 else "assistant",
                content=_content(
                    f"第{seq}条：配置路径 /etc/noesis/secret-{prefix}.yaml"
                ),
                message_sequence=seq,
                created_at=seq,
            )
            for seq in range(1, 8)
        ]
        rows.append(
            TChatMessage(
                id=f"{prefix}-other-1",
                session_id=other_id,
                user_id=str(uuid.uuid4()),
                role="user",
                content=_content(f"他人也提到 /etc/noesis/secret-{prefix}.yaml"),
                message_sequence=1,
                created_at=1,
            )
        )
        db.add_all(rows)
        await db.commit()

        try:
            # 检索形态：中文关键词 + 具体路径命中
            result = await search_session_history(
                db, user_id=user_id, session_id=session_id,
                query=f"secret-{prefix}", limit=10,
            )
            assert result.mode == "search"
            assert [h.sequence for h in result.hits] == list(range(1, 8))

            # 滚动形态：目标序号 ± 窗口
            scroll = await search_session_history(
                db, user_id=user_id, session_id=session_id,
                around_sequence=4, window=1,
            )
            assert [h.sequence for h in scroll.hits] == [3, 4, 5]

            # 边界缺失 → 降级全历史
            degraded = await search_session_history(
                db, user_id=user_id, session_id=session_id,
                query=f"secret-{prefix}", before_compaction=True,
            )
            assert degraded.boundary_unknown is True
            assert degraded.hits

            # 归属不符 → 拒绝
            with pytest.raises(SessionAccessDenied):
                await search_session_history(
                    db, user_id=user_id, session_id=other_id, query="secret"
                )

            # 跨会话：只搜到自己的会话，排除当前
            groups = await search_user_sessions(
                db, user_id=user_id, exclude_session_id=session_id,
                query=f"secret-{prefix}", limit=5,
            )
            assert groups == []

            # 边界写入：先放一个活跃 run 骨架行（seq 8），边界应排除它取 7
            skeleton_id = f"{prefix}-skeleton"
            db.add(
                TChatMessage(
                    id=skeleton_id,
                    session_id=session_id,
                    user_id=user_id,
                    role="assistant",
                    content={"parts": []},
                    status="streaming",
                    message_sequence=8,
                    created_at=99,
                )
            )
            await db.commit()
            db.add(
                TAgentRun(
                    id=f"run-{prefix}",
                    user_id=user_id,
                    session_id=session_id,
                    assistant_message_id=skeleton_id,
                    client_request_id=f"cr-{prefix}",
                    request_digest="x" * 64,
                    qa_type="SUPER_AGENT_QA",
                    status="running",
                    last_sequence=0,
                    attempt_id=1,
                    created_at=99,
                    updated_at=99,
                )
            )
            await db.commit()

            cutoff = await record_compaction_boundary(db, session_id=session_id)
            assert cutoff == 7
            row = await db.execute(
                select(TChatSession.compaction_cutoff_seq).where(
                    TChatSession.id == session_id
                )
            )
            assert row.scalar_one() == 7

            # 边界过滤：只命中 seq <= 7（全部 7 条都在边界内）
            bounded = await search_session_history(
                db, user_id=user_id, session_id=session_id,
                query=f"secret-{prefix}", before_compaction=True, limit=10,
            )
            assert bounded.boundary_unknown is False
            assert [h.sequence for h in bounded.hits] == list(range(1, 8))

            # 边界写入幂等保护：更小值不回写
            await db.execute(
                TChatSession.__table__.update()
                .where(TChatSession.id == session_id)
                .values(compaction_cutoff_seq=99)
            )
            await db.commit()
            again = await record_compaction_boundary(db, session_id=session_id)
            assert again == 7
            row = await db.execute(
                select(TChatSession.compaction_cutoff_seq).where(
                    TChatSession.id == session_id
                )
            )
            assert row.scalar_one() == 99
        finally:
            await db.execute(
                delete(TAgentRun).where(TAgentRun.session_id == session_id)
            )
            await db.execute(
                delete(TChatMessage).where(TChatMessage.session_id.in_([session_id, other_id]))
            )
            await db.execute(
                delete(TChatSession).where(
                    TChatSession.id.in_([session_id, other_id])
                )
            )
            await db.commit()
