"""md 文件记忆层：文件服务与抽取守卫测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import noesis.config.user_data_paths as user_data_paths
from noesis.services.memory import extraction
from noesis.services.memory.store import MemoryStore


@pytest.fixture()
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "users"
    monkeypatch.setattr(user_data_paths, "_USERS_ROOT", root)
    return root


def test_layout_creates_five_type_dirs_and_index(users_root: Path) -> None:
    MemoryStore.ensure_layout("u1")
    memory = users_root / "u1" / "memory"
    assert (memory / "MEMORY.md").is_file()
    for type_dir in ("preference", "goal", "decision", "experience", "gotcha"):
        assert (memory / type_dir).is_dir()
    assert (memory / "journal").is_dir()


def test_upsert_new_entry_creates_file_and_index_line(users_root: Path) -> None:
    entry = MemoryStore.upsert_entry(
        "u1",
        memory_type="preference",
        label="文档格式",
        body="一律表格、简体中文",
        why="阅读快",
        applicability="写文档时",
        sources=["会话 abc12345 · 2026-08-26"],
    )
    assert entry.rel_path.endswith(".md")
    path = users_root / "u1" / "memory" / entry.rel_path
    text = path.read_text(encoding="utf-8")
    assert "# 文档格式" in text
    assert "一律表格、简体中文" in text
    assert "**Why**" in text and "阅读快" in text
    assert "会话 abc12345" in text
    state = MemoryStore.read_index("u1")
    assert [e.label for e in state.entries] == ["文档格式"]


def test_same_label_merges_and_appends_source(users_root: Path) -> None:
    first = MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="文档格式", body="v1", sources=["s1"]
    )
    second = MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="文档格式", body="v2", sources=["s2"]
    )
    assert second.slug == first.slug
    text = (users_root / "u1" / "memory" / first.rel_path).read_text(encoding="utf-8")
    assert "v2" in text
    assert "s1" in text and "s2" in text
    assert len(MemoryStore.read_index("u1").entries) == 1


def test_slug_collision_appends_serial(users_root: Path) -> None:
    first = MemoryStore.upsert_entry(
        "u1", memory_type="decision", label="package manager", body="pnpm", sources=[]
    )
    manual = users_root / "u1" / "memory" / "decision" / f"{first.slug}-manual.md"
    manual.write_text("# 占位", encoding="utf-8")
    # manual 文件无索引行 → unique_slug 应避开撞名
    other = MemoryStore.unique_slug("u1", "decision", first.slug)
    assert other != first.slug


def test_index_corrupt_lines_skipped_and_rebuildable(users_root: Path) -> None:
    entry = MemoryStore.upsert_entry(
        "u1", memory_type="gotcha", label="边界", body="不要越过工作区", sources=["s"]
    )
    index = users_root / "u1" / "memory" / "MEMORY.md"
    index.write_text(
        "## 注意事项\n- [坏行] 没有箭头\n"
        f"- [边界] 不要越过工作区 → {entry.rel_path}\n",
        encoding="utf-8",
    )
    state = MemoryStore.read_index("u1")
    assert state.corrupt_lines == 1
    assert len(state.entries) == 1
    rebuilt = MemoryStore.rebuild_index("u1")
    assert rebuilt.corrupt_lines == 0
    assert [e.label for e in rebuilt.entries] == ["边界"]


def test_remove_entry_keeps_journal(users_root: Path) -> None:
    entry = MemoryStore.upsert_entry(
        "u1", memory_type="goal", label="学习路线", body="在学 Node.js", sources=["s"]
    )
    MemoryStore.append_journal("u1", session_id="sess-1234", text="讨论学习路线")
    assert MemoryStore.remove_entry("u1", "goal", entry.slug)
    assert not (users_root / "u1" / "memory" / entry.rel_path).is_file()
    assert MemoryStore.read_index("u1").entries == []
    journal_files = list((users_root / "u1" / "memory" / "journal").glob("*.md"))
    assert len(journal_files) == 1
    assert "讨论学习路线" in journal_files[0].read_text(encoding="utf-8")


def test_journal_is_append_only(users_root: Path) -> None:
    MemoryStore.append_journal("u1", session_id="sess-a", text="第一段")
    MemoryStore.append_journal("u1", session_id="sess-b", text="第二段")
    files = list((users_root / "u1" / "memory" / "journal").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "第一段" in text and "第二段" in text


def test_search_matches_entry_content(users_root: Path) -> None:
    MemoryStore.upsert_entry(
        "u1", memory_type="experience", label="重试", body="超时后指数退避重试", sources=[]
    )
    MemoryStore.upsert_entry(
        "u1", memory_type="gotcha", label="边界", body="工作区外写入会失败", sources=[]
    )
    hits = MemoryStore.search("u1", "重试")
    assert len(hits) == 1
    assert hits[0]["memory_type"] == "experience"
    assert MemoryStore.search("u1", "不存在的关键词") == []


def test_invalid_type_rejected(users_root: Path) -> None:
    with pytest.raises(ValueError, match="非法记忆类型"):
        MemoryStore.upsert_entry(
            "u1", memory_type="workflow", label="x", body="y", sources=[]
        )


# ----- frontmatter：渲染/解析往返、容错、索引投影 -----


def test_frontmatter_round_trip(users_root: Path) -> None:
    entry = MemoryStore.upsert_entry(
        "u1",
        memory_type="preference",
        label="文档格式",
        body="一律表格、简体中文",
        why="阅读快",
        applicability="写文档时",
        description="偏好表格化简体中文输出；涉及文档/报告/说明输出时调用",
        sources=["会话 abc12345 · 2026-08-26"],
    )
    path = users_root / "u1" / "memory" / entry.rel_path
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "type: preference" in text
    assert "description: 偏好表格化简体中文输出；涉及文档/报告/说明输出时调用" in text
    assert "**来源**" not in text  # 散文来源行迁入 frontmatter
    assert "**更新时间**" not in text
    parsed = MemoryStore.read_entry_file(path)
    assert parsed["memory_type"] == "preference"
    assert parsed["label"] == "文档格式"
    assert parsed["description"] == "偏好表格化简体中文输出；涉及文档/报告/说明输出时调用"
    assert parsed["body"] == "一律表格、简体中文"
    assert parsed["why"] == "阅读快"
    assert parsed["applicability"] == "写文档时"
    assert parsed["sources"] == ["会话 abc12345 · 2026-08-26"]
    assert parsed["created"] and parsed["updated_at"]


def test_update_preserves_description_created_and_merges_sources(users_root: Path) -> None:
    """更新路径：未提供新 description 保留既有值（防清空权威字段）、
    created 不被重置、来源去重追加（引擎改写幂等）。"""
    first = MemoryStore.upsert_entry(
        "u1",
        memory_type="decision",
        label="包管理",
        body="统一 pnpm",
        description="包管理器统一 pnpm；新增前端项目或讨论工具链选型时调用",
        sources=["会话 aaaa0000 · 2026-08-01"],
    )
    path = users_root / "u1" / "memory" / first.rel_path
    created_before = MemoryStore.read_entry_file(path)["created"]
    MemoryStore.upsert_entry(
        "u1",
        memory_type="decision",
        label="包管理",
        body="统一 pnpm（workspace 用 pnpm -w）",
        sources=["会话 bbbb0000 · 2026-09-01", "会话 aaaa0000 · 2026-08-01"],
    )
    parsed = MemoryStore.read_entry_file(path)
    assert parsed["description"] == "包管理器统一 pnpm；新增前端项目或讨论工具链选型时调用"
    assert parsed["created"] == created_before
    assert parsed["sources"] == ["会话 aaaa0000 · 2026-08-01", "会话 bbbb0000 · 2026-09-01"]
    # 索引行 = frontmatter description 的投影
    state = MemoryStore.read_index("u1")
    assert state.entries[0].description == parsed["description"]


def test_legacy_prose_entry_parses_via_fallback(users_root: Path) -> None:
    """存量散文条目不迁移：散文容错路径长期保留。"""
    entry = MemoryStore.upsert_entry(
        "u1", memory_type="gotcha", label="边界", body="不要越过工作区", sources=["s0"]
    )
    path = users_root / "u1" / "memory" / entry.rel_path
    legacy = (
        "# 边界\n\n不要越过工作区（旧格式）\n\n"
        "**Why**\n安全边界\n\n**适用条件**\n执行文件操作时\n\n"
        "**来源**\n- 会话 cccc0000 · 2026-07-01\n\n**更新时间** 2026-07-01\n"
    )
    path.write_text(legacy, encoding="utf-8")
    parsed = MemoryStore.read_entry_file(path)
    assert parsed["label"] == "边界"
    assert parsed["body"] == "不要越过工作区（旧格式）"
    assert parsed["sources"] == ["会话 cccc0000 · 2026-07-01"]
    assert parsed["updated_at"] == "2026-07-01"
    assert parsed["description"] == "不要越过工作区（旧格式）"
    assert parsed["memory_type"] == "gotcha"  # type 取自目录


def test_broken_frontmatter_degrades_to_prose(users_root: Path) -> None:
    """frontmatter YAML 写坏 → 退化为无元数据条目，检索与重建不失败。"""
    entry = MemoryStore.upsert_entry(
        "u1", memory_type="experience", label="重试", body="指数退避", sources=["s"]
    )
    path = users_root / "u1" / "memory" / entry.rel_path
    path.write_text(
        "---\ntype: experience\nlabel: 坏掉的: frontmatter\n  bad indent\n---\n\n# 重试\n\n指数退避\n",
        encoding="utf-8",
    )
    parsed = MemoryStore.read_entry_file(path)
    assert parsed["body"]  # 正文照常可读
    assert parsed["memory_type"] == "experience"  # type 取自目录
    assert MemoryStore.search("u1", "指数退避")  # 检索不失败
    rebuilt = MemoryStore.rebuild_index("u1")  # 索引重建不失败
    assert [e.label for e in rebuilt.entries] == ["重试"]


def test_legacy_entry_upgraded_on_engine_write(users_root: Path) -> None:
    """存量条目被引擎写入时自然升级为 frontmatter（散文解析结果迁入字段）。"""
    directory = users_root / "u1" / "memory" / "goal"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "nodejs.md"
    path.write_text(
        "# 学习路线\n\n在学 Node.js\n\n**来源**\n- 会话 dddd0000 · 2026-08-01\n\n**更新时间** 2026-08-01\n",
        encoding="utf-8",
    )
    MemoryStore.upsert_entry(
        "u1",
        memory_type="goal",
        label="学习路线",
        body="在学 Node.js（已到异步篇）",
        sources=["会话 eeee0000 · 2026-09-01"],
        slug="nodejs",
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    parsed = MemoryStore.read_entry_file(path)
    assert parsed["sources"] == ["会话 dddd0000 · 2026-08-01", "会话 eeee0000 · 2026-09-01"]
    assert parsed["body"] == "在学 Node.js（已到异步篇）"


def test_index_projection_overrides_manual_index_edit(users_root: Path) -> None:
    """用户只手改索引行 description：条目下次引擎写入时被 frontmatter 覆盖。"""
    entry = MemoryStore.upsert_entry(
        "u1",
        memory_type="preference",
        label="文档格式",
        body="一律表格",
        description="偏好表格化输出；写文档时调用",
        sources=["s"],
    )
    index = users_root / "u1" / "memory" / "MEMORY.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "偏好表格化输出；写文档时调用", "手改的索引描述"
        ),
        encoding="utf-8",
    )
    assert MemoryStore.read_index("u1").entries[0].description == "手改的索引描述"
    MemoryStore.upsert_entry(
        "u1",
        memory_type="preference",
        label="文档格式",
        body="一律表格、带脚注",
        sources=["s2"],
        slug=entry.slug,
    )
    assert (
        MemoryStore.read_index("u1").entries[0].description
        == "偏好表格化输出；写文档时调用"
    )


# ----- 抽取守卫 -----


def _result(entries, journal="摘要", excluded=()):
    return extraction.ExtractionResult(
        entries=entries, journal_summary=journal, excluded=list(excluded)
    )


@pytest.mark.asyncio
async def test_apply_restatement_of_recalled_entry_is_skipped(
    users_root: Path,
) -> None:
    entry = MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="文档格式", body="一律表格", sources=["s0"]
    )
    result = _result(
        [extraction.ExtractedEntry(memory_type="preference", label="文档格式", body="一律表格")]
    )
    await extraction.MemoryExtractionService._apply(
        user_id="u1",
        session_id="sess-restated",
        result=result,
        recalled=[entry.rel_path],
    )
    text = (users_root / "u1" / "memory" / entry.rel_path).read_text(encoding="utf-8")
    assert "sess-restated" not in text  # 复述未追加来源


@pytest.mark.asyncio
async def test_apply_correction_of_recalled_entry_updates(users_root: Path) -> None:
    entry = MemoryStore.upsert_entry(
        "u1", memory_type="decision", label="包管理", body="统一 pnpm", sources=["s0"]
    )
    result = _result(
        [
            extraction.ExtractedEntry(
                memory_type="decision",
                label="包管理",
                body="改用 bun",
                update_existing_label="包管理",
                is_correction=True,
            )
        ]
    )
    await extraction.MemoryExtractionService._apply(
        user_id="u1",
        session_id="sess-correct",
        result=result,
        recalled=[entry.rel_path],
    )
    text = (users_root / "u1" / "memory" / entry.rel_path).read_text(encoding="utf-8")
    assert "改用 bun" in text
    assert "会话 sess-cor" in text


@pytest.mark.asyncio
async def test_apply_caps_new_entries_and_drops_extra(users_root: Path) -> None:
    result = _result(
        [
            extraction.ExtractedEntry(memory_type="goal", label=f"g{i}", body=f"v{i}")
            for i in range(5)
        ]
    )
    await extraction.MemoryExtractionService._apply(
        user_id="u1", session_id="sess-cap", result=result, recalled=[]
    )
    state = MemoryStore.read_index("u1")
    assert len(state.entries) == 3  # max_entries_per_extraction


@pytest.mark.asyncio
async def test_apply_invalid_type_goes_nowhere(users_root: Path) -> None:
    result = _result(
        [extraction.ExtractedEntry(memory_type="workflow", label="w", body="b")]
    )
    await extraction.MemoryExtractionService._apply(
        user_id="u1", session_id="sess-type", result=result, recalled=[]
    )
    assert MemoryStore.read_index("u1").entries == []
    assert list((users_root / "u1" / "memory" / "journal").glob("*.md"))


@pytest.mark.asyncio
async def test_apply_zero_value_session_writes_nothing(users_root: Path) -> None:
    result = extraction.ExtractionResult(entries=[], journal_summary="")
    await extraction.MemoryExtractionService._apply(
        user_id="u1", session_id="sess-empty", result=result, recalled=[]
    )
    assert MemoryStore.read_index("u1").entries == []
    assert list((users_root / "u1" / "memory" / "journal").glob("*.md")) == []


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _SegmentDB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_a, **_k):
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_extract_session_marks_and_skips_when_disabled(
    users_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    from noesis.services.memory.user_settings import MemoryUserSettings

    monkeypatch.setattr(MemoryUserSettings, "is_enabled", staticmethod(lambda _u: False))
    session = MagicMock(id="sess-off", memory_extracted_seq=7)
    db = SimpleNamespace(
        get=AsyncMock(return_value=session),
        execute=AsyncMock(return_value=_FakeResult([_msg(1, "user", "hi")])),
        commit=AsyncMock(),
    )
    assert await extraction.MemoryExtractionService.extract_session(
        db, session_id="sess-off", user_id="u1"
    )
    assert db.commit.await_count == 1


# ----- 水位增量（bridge / head-tail / 成功才推进） -----


from collections import namedtuple

_MsgRow = namedtuple("_MsgRow", ["message_sequence", "role", "content"])


def _msg(seq: int, role: str, text: str):
    return _MsgRow(seq, role, {"parts": [{"type": "text", "content": text}]})


@pytest.mark.asyncio
async def test_load_segment_includes_bridge_and_new_messages() -> None:
    rows = [
        _msg(1, "user", "第一轮"),
        _msg(2, "assistant", "回答一"),
        _msg(3, "user", "第二轮"),
        _msg(4, "assistant", "回答二"),
    ]
    text, new_max = await extraction.MemoryExtractionService._load_segment(
        _SegmentDB(rows), "s", watermark=2
    )
    assert new_max == 4
    # 水位=2：消息 1、2 都是背景（恰 2 条全带）
    assert "[背景] [user] 第一轮" in text
    assert "[背景] [assistant] 回答一" in text
    assert "[user] 第二轮" in text  # 新段无背景前缀
    assert "---" in text  # 背景与新段分隔


@pytest.mark.asyncio
async def test_load_segment_head_tail_truncation_keeps_both_ends(
    users_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import noesis.services.memory.extraction as ext

    rows = [_msg(i, "user", f"消息{i}" * 50) for i in range(1, 21)]
    from types import SimpleNamespace

    monkeypatch.setattr(
        ext, "MemoryConfig", SimpleNamespace(max_message_chars=800)
    )
    text, new_max = await ext.MemoryExtractionService._load_segment(
        _SegmentDB(rows), "s", watermark=None
    )
    assert new_max == 20
    assert "消息1" in text  # 段头保留（目标陈述）
    assert "消息20" in text  # 段尾保留（结论）
    assert "（中间消息因长度上限省略）" in text


@pytest.mark.asyncio
async def test_watermark_advanced_only_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 失败 → _mark_extracted 不被调用（水位不动，下次重试）。"""
    from unittest.mock import AsyncMock, MagicMock

    from noesis.services.memory.extraction import MemoryExtractionService
    from noesis.services.memory.user_settings import MemoryUserSettings

    monkeypatch.setattr(MemoryUserSettings, "is_enabled", staticmethod(lambda _u: True))
    session = MagicMock(id="s", memory_extracted_seq=None)
    db = SimpleNamespace(
        get=AsyncMock(return_value=session),
        execute=AsyncMock(return_value=_FakeResult([_msg(1, "user", "hi")])),
        commit=AsyncMock(),
    )

    async def boom(**_kw):
        raise RuntimeError("llm down")

    from unittest.mock import patch

    with (
        patch.object(MemoryExtractionService, "_load_recalled", AsyncMock(return_value=[])),
        patch.object(MemoryExtractionService, "_run_llm", boom),
    ):
        with pytest.raises(RuntimeError):
            await MemoryExtractionService.extract_session(db, session_id="s", user_id="u1")
    db.commit.assert_not_awaited()  # 水位未推进


@pytest.mark.asyncio
async def test_subagent_sessions_excluded_from_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """sweep 只捞 kind=root（subagent 结论经父会话通知回流，不直接抽）。"""
    from noesis.services.memory.extraction import MemoryExtractionService

    captured = {}

    class _Result:
        def all(self):
            return []

    async def fake_execute(stmt, *_a, **_k):
        captured["query"] = str(stmt)
        return _Result()

    class _Ctx:
        async def __aenter__(self):
            return SimpleNamespace(execute=fake_execute)

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(
        "noesis.services.memory.extraction.pg_manager.get_async_session_context",
        _Ctx,
    )
    await MemoryExtractionService.sweep_once()
    assert "t_chat_session.kind" in captured["query"]


def test_updated_at_preserved_by_mark_extracted_statement() -> None:
    """标记语句显式携带 updated_at 自身（抑制 onupdate，不扰动会话排序）。"""
    import asyncio

    from noesis.services.memory.extraction import MemoryExtractionService

    async def run():
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        await MemoryExtractionService._mark_extracted(db, "s", 42)
        stmt = db.execute.await_args.args[0]
        compiled = str(stmt)
        assert "memory_extracted_seq" in compiled
        assert "updated_at" in compiled

    asyncio.run(run())


# ----- 整理任务 -----


@pytest.mark.asyncio
async def test_consolidation_remove_and_merge(users_root: Path) -> None:
    from noesis.services.memory.consolidation import (
        ConsolidateAction,
        MemoryConsolidationService as MCS,
    )

    keep = MemoryStore.upsert_entry(
        "u1", memory_type="experience", label="索引重建", body="超预算时重建索引", sources=["s"]
    )
    dup = MemoryStore.upsert_entry(
        "u1", memory_type="experience", label="重复经验", body="超预算时重建索引（重复）", sources=["s2"]
    )
    stale = MemoryStore.upsert_entry(
        "u1", memory_type="goal", label="临时目标", body="一次性的旧目标", sources=["s"]
    )
    result = type("R", (), {"actions": [
        ConsolidateAction(op="merge", target=dup.rel_path, merge_into=keep.rel_path,
                          new_body="超预算时重建索引", new_label="索引重建"),
        ConsolidateAction(op="remove", target=stale.rel_path, reason="目标已完结"),
    ]})()
    applied = MCS._apply("u1", result, 0)
    assert applied == 3  # merge 记 2、remove 记 1
    state = MemoryStore.read_index("u1")
    assert [e.slug for e in state.entries] == [keep.slug]
    text = (users_root / "u1" / "memory" / keep.rel_path).read_text(encoding="utf-8")
    assert "s2" in text  # 被合并条目的来源并入
    assert not (users_root / "u1" / "memory" / stale.rel_path).is_file()


@pytest.mark.asyncio
async def test_consolidation_drops_dead_index_lines(users_root: Path) -> None:
    from noesis.services.memory.consolidation import (
        MemoryConsolidationService as MCS,
    )
    from noesis.services.memory.store import IndexEntry

    entry = MemoryStore.upsert_entry(
        "u1", memory_type="gotcha", label="边界", body="不要越过", sources=["s"]
    )
    state = MemoryStore.read_index("u1")
    MemoryStore.write_index("u1", [*state.entries, IndexEntry(
        memory_type="gotcha", slug="ghost", label="幽灵", description="不存在的文件")])
    removed = MCS._drop_dead_entries("u1", MemoryStore.read_index("u1"))
    assert removed == 1
    assert [e.slug for e in MemoryStore.read_index("u1").entries] == [entry.slug]


# ----- 消息格式解析（{"parts": [...]} 落库形态回归） -----


def test_message_text_parses_parts_dict_format() -> None:
    text = extraction.MemoryExtractionService._message_text(
        {"parts": [
            {"type": "reasoning", "content": "思考过程"},
            {"type": "text", "content": "正式回答"},
        ]}
    )
    assert text == "正式回答"


def test_message_text_tolerates_legacy_formats() -> None:
    svc = extraction.MemoryExtractionService
    assert svc._message_text("裸字符串") == "裸字符串"
    assert svc._message_text([{"type": "text", "content": "a"}, "b"]) == "a\nb"
    assert svc._message_text(None) == ""
    assert svc._message_text({"parts": "整段文本"}) == "整段文本"


# ----- 抽取标记不改变会话排序（updated_at 保全） -----


@pytest.mark.asyncio
async def test_mark_extracted_preserves_updated_at() -> None:
    """Core update 会触发列 onupdate；显式携带自身列抑制之（回归）。"""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from noesis.storage.postgres.models.chat import TChatSession

    engine = create_engine("sqlite://")
    TChatSession.metadata.tables["t_chat_session"].create(engine)
    original = 1_700_000_000_000
    with Session(engine, expire_on_commit=False) as sync_db:
        sync_db.add(TChatSession(
            id="sess-order",
            user_id="00000000-0000-0000-0000-000000000001",
            title="t", kind="root",
            created_at=original, updated_at=original,
        ))
        sync_db.commit()

        import time as _time
        from sqlalchemy import update as _update

        # 与 MemoryExtractionService._mark_extracted 相同的语句形态
        sync_db.execute(
            _update(TChatSession)
            .where(TChatSession.id == "sess-order")
            .values(
                memory_extracted_at=int(_time.time() * 1000),
                updated_at=TChatSession.updated_at,
            )
        )
        sync_db.commit()
        session = sync_db.execute(
            select(TChatSession).where(TChatSession.id == "sess-order")
        ).scalar_one()
        assert session.memory_extracted_at is not None
        assert session.updated_at == original  # onupdate 被抑制
    engine.dispose()


# ----- 抽取决策落 journal（含排除理由） -----


@pytest.mark.asyncio
async def test_apply_records_extraction_decisions_in_journal(users_root: Path) -> None:
    result = _result(
        [
            extraction.ExtractedEntry(
                memory_type="preference",
                label="文档格式",
                body="一律表格",
                description="偏好表格化输出；写文档时调用",
                reason="用户两次纠正输出格式",
            )
        ],
        journal="讨论文档格式",
        excluded=[
            extraction.ExcludedItem(gist="本次部署的临时端口", reason="临时任务状态")
        ],
    )
    await extraction.MemoryExtractionService._apply(
        user_id="u1", session_id="sess-dec", result=result, recalled=[]
    )
    text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (users_root / "u1" / "memory" / "journal").glob("*.md")
    )
    assert "（抽取决策）" in text
    assert "- 新建：preference/" in text
    assert "用户两次纠正输出格式" in text
    assert "本次部署的临时端口" in text and "临时任务状态" in text
    # description 进入 frontmatter 并投影到索引行
    state = MemoryStore.read_index("u1")
    assert state.entries[0].description == "偏好表格化输出；写文档时调用"


@pytest.mark.asyncio
async def test_apply_exclusions_only_still_records_decisions(users_root: Path) -> None:
    """零新条目但有排除：决策块仍落 journal（spec 场景）。"""
    result = extraction.ExtractionResult(
        entries=[],
        excluded=[
            extraction.ExcludedItem(gist="git 历史可得的信息", reason="代码本身可得")
        ],
        journal_summary="技术闲聊",
    )
    await extraction.MemoryExtractionService._apply(
        user_id="u1", session_id="sess-excl", result=result, recalled=[]
    )
    assert MemoryStore.read_index("u1").entries == []
    journal = list((users_root / "u1" / "memory" / "journal").glob("*.md"))
    text = journal[0].read_text(encoding="utf-8")
    assert "（抽取决策）" in text
    assert "git 历史可得的信息" in text


@pytest.mark.asyncio
async def test_apply_restatement_exclusion_recorded(users_root: Path) -> None:
    """防自强化拦截也进决策块（线上坏记忆可对账）。"""
    entry = MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="文档格式", body="一律表格", sources=["s0"]
    )
    result = _result(
        [extraction.ExtractedEntry(memory_type="preference", label="文档格式", body="一律表格")]
    )
    await extraction.MemoryExtractionService._apply(
        user_id="u1",
        session_id="sess-restated",
        result=result,
        recalled=[entry.rel_path],
    )
    text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (users_root / "u1" / "memory" / "journal").glob("*.md")
    )
    assert "复述本轮召回条目" in text


# ----- 抽取/整理 prompt 防呆断言 -----


def test_extraction_prompt_contains_routing_guardrails() -> None:
    prompt = extraction._EXTRACTION_PROMPT
    # 时效性禁入稳定类型（硬规则）
    assert "只能进 goal" in prompt
    assert "不得写入 preference/decision/experience/gotcha" in prompt
    # 灰色地带 few-shot 对照示例
    assert "定了包管理用 pnpm」→ decision" in prompt
    assert "就是喜欢 pnpm 的简洁」→ preference" in prompt
    assert "Q3 要做完迁移」→ goal" in prompt
    # description 两段式约束
    assert "何时调用" in prompt


def test_consolidation_prompt_contains_priority_rules() -> None:
    from noesis.services.memory.consolidation import _CONSOLIDATION_PROMPT

    prompt = _CONSOLIDATION_PROMPT
    assert "用户显式修正" in prompt
    assert "稳定类型" in prompt
    assert "时间与证据" in prompt
    assert "不得静默改写" in prompt
    assert "new_description" in prompt


# ----- 整理：改写/淘汰前快照 + description 同步 + type 归位 -----


@pytest.mark.asyncio
async def test_consolidation_snapshots_and_syncs_description(users_root: Path) -> None:
    from noesis.services.memory.consolidation import (
        ConsolidateAction,
        MemoryConsolidationService as MCS,
    )

    keep = MemoryStore.upsert_entry(
        "u1", memory_type="experience", label="索引重建", body="超预算时重建索引", sources=["s1"]
    )
    dup = MemoryStore.upsert_entry(
        "u1",
        memory_type="experience",
        label="重复经验",
        body="超预算时重建索引（重复）",
        description="重复条目；测试用",
        sources=["s2"],
    )
    stale = MemoryStore.upsert_entry(
        "u1", memory_type="goal", label="临时目标", body="一次性的旧目标", sources=["s3"]
    )
    dup_text_before = (users_root / "u1" / "memory" / dup.rel_path).read_text(encoding="utf-8")
    result = type("R", (), {"actions": [
        ConsolidateAction(
            op="merge", target=dup.rel_path, merge_into=keep.rel_path,
            new_body="超预算时重建索引", new_label="索引重建",
        ),
        ConsolidateAction(
            op="rewrite", target=keep.rel_path, new_body="超预算时重建索引（v2）",
            new_description="索引超预算时从条目目录重建；整理压缩索引时调用",
        ),
        ConsolidateAction(op="remove", target=stale.rel_path, reason="目标已完结"),
    ]})()
    applied = MCS._apply("u1", result, 0)
    assert applied == 4  # merge 记 2、rewrite 记 1、remove 记 1
    journal_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (users_root / "u1" / "memory" / "journal").glob("*.md")
    )
    # merge 双方、rewrite/remove 目标的改前全文快照（含 frontmatter）落 journal
    for rel in (dup.rel_path, keep.rel_path, stale.rel_path):
        assert f"（整理快照 · 原条目 {rel}）" in journal_text
    assert dup_text_before.strip() in journal_text  # 快照含 frontmatter 原文
    assert not (users_root / "u1" / "memory" / stale.rel_path).is_file()
    # rewrite 后 description 同步（new_description 生效）
    kept = MemoryStore.read_entry("u1", "experience", keep.slug)
    assert kept["description"] == "索引超预算时从条目目录重建；整理压缩索引时调用"
    assert MemoryStore.read_index("u1").entries[0].description == kept["description"]


@pytest.mark.asyncio
async def test_consolidation_rewrite_without_description_preserves(users_root: Path) -> None:
    from noesis.services.memory.consolidation import (
        ConsolidateAction,
        MemoryConsolidationService as MCS,
    )

    entry = MemoryStore.upsert_entry(
        "u1",
        memory_type="decision",
        label="包管理",
        body="统一 pnpm",
        description="包管理器统一 pnpm；选型讨论时调用",
        sources=["s"],
    )
    result = type("R", (), {"actions": [
        ConsolidateAction(op="rewrite", target=entry.rel_path, new_body="统一 pnpm（corepack）"),
    ]})()
    assert MCS._apply("u1", result, 0) == 1
    parsed = MemoryStore.read_entry("u1", "decision", entry.slug)
    assert parsed["body"] == "统一 pnpm（corepack）"
    assert parsed["description"] == "包管理器统一 pnpm；选型讨论时调用"  # 留空保留原值


def test_consolidation_aligns_frontmatter_type(users_root: Path) -> None:
    from noesis.services.memory.consolidation import (
        MemoryConsolidationService as MCS,
    )

    entry = MemoryStore.upsert_entry(
        "u1", memory_type="preference", label="文档格式", body="一律表格", sources=["s"]
    )
    path = users_root / "u1" / "memory" / entry.rel_path
    # 模拟用户挪动文件后 frontmatter type 与目录不一致
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: preference", "type: goal"),
        encoding="utf-8",
    )
    fixes = MCS._align_frontmatter_types("u1")
    assert fixes == [f"preference/{entry.slug}.md（frontmatter goal → preference）"]
    assert "type: preference" in path.read_text(encoding="utf-8")
    # 文件未移动、其余字段保留
    assert path.is_file()
    parsed = MemoryStore.read_entry_file(path)
    assert parsed["label"] == "文档格式" and parsed["body"] == "一律表格"
    # 归位动作记录进 journal
    journal_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (users_root / "u1" / "memory" / "journal").glob("*.md")
    )
    assert "整理归位" in journal_text


def test_recent_journal_excludes_snapshot_blocks(users_root: Path) -> None:
    from noesis.services.memory.consolidation import (
        MemoryConsolidationService as MCS,
    )

    MemoryStore.append_journal("u1", session_id="sess-1", text="正常情景块")
    MemoryStore.append_journal(
        "u1",
        session_id=None,
        text="快照正文（不应进入整理信号）",
        label="整理快照 · 原条目 goal/x.md",
    )
    journal = MCS._recent_journal("u1")
    assert "正常情景块" in journal
    assert "快照正文" not in journal


def test_recent_journal_strips_snapshot_with_embedded_headers(users_root: Path) -> None:
    """快照正文内嵌 ``## `` 标题行时整块剔除，不漏进整理信号（回归）。"""
    from noesis.services.memory.consolidation import (
        MemoryConsolidationService as MCS,
    )

    MemoryStore.append_journal("u1", session_id="sess-a", text="正常情景块")
    MemoryStore.append_journal(
        "u1",
        session_id=None,
        text="快照正文首行\n\n## 内嵌小节（非时间戳块头）\n\n快照正文尾巴",
        label="整理快照 · 原条目 goal/x.md",
    )
    MemoryStore.append_journal("u1", session_id="sess-b", text="后续正常块")
    journal = MCS._recent_journal("u1")
    assert "正常情景块" in journal and "后续正常块" in journal
    assert "快照正文首行" not in journal
    assert "内嵌小节" not in journal
    assert "快照正文尾巴" not in journal


def test_index_renders_no_empty_group_headers(users_root: Path) -> None:
    """空组不渲染：索引是路由面，空库不输出五个裸组头。"""
    MemoryStore.ensure_layout("u1")
    index = users_root / "u1" / "memory" / "MEMORY.md"
    text = index.read_text(encoding="utf-8")
    assert "## 偏好" not in text and "## 注意事项" not in text
    assert "Noesis 记忆索引" in text  # 注释头保留

    entry = MemoryStore.upsert_entry(
        "u1", memory_type="gotcha", label="边界", body="不要越过工作区", sources=["s"]
    )
    text = index.read_text(encoding="utf-8")
    assert "## 注意事项" in text and entry.rel_path in text
    assert "## 偏好" not in text, "仍空的组不出现"
