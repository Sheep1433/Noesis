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


# ----- 抽取守卫 -----


def _result(entries, journal="摘要"):
    return extraction.ExtractionResult(entries=entries, journal_summary=journal)


@pytest.mark.asyncio
async def test_apply_restatement_of_injected_entry_is_skipped(
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
        injected=[entry.rel_path],
    )
    text = (users_root / "u1" / "memory" / entry.rel_path).read_text(encoding="utf-8")
    assert "sess-restated" not in text  # 复述未追加来源


@pytest.mark.asyncio
async def test_apply_correction_of_injected_entry_updates(users_root: Path) -> None:
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
        injected=[entry.rel_path],
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
        user_id="u1", session_id="sess-cap", result=result, injected=[]
    )
    state = MemoryStore.read_index("u1")
    assert len(state.entries) == 3  # max_entries_per_extraction


@pytest.mark.asyncio
async def test_apply_invalid_type_goes_nowhere(users_root: Path) -> None:
    result = _result(
        [extraction.ExtractedEntry(memory_type="workflow", label="w", body="b")]
    )
    await extraction.MemoryExtractionService._apply(
        user_id="u1", session_id="sess-type", result=result, injected=[]
    )
    assert MemoryStore.read_index("u1").entries == []
    assert list((users_root / "u1" / "memory" / "journal").glob("*.md"))


@pytest.mark.asyncio
async def test_apply_zero_value_session_writes_nothing(users_root: Path) -> None:
    result = extraction.ExtractionResult(entries=[], journal_summary="")
    await extraction.MemoryExtractionService._apply(
        user_id="u1", session_id="sess-empty", result=result, injected=[]
    )
    assert MemoryStore.read_index("u1").entries == []
    assert list((users_root / "u1" / "memory" / "journal").glob("*.md")) == []


@pytest.mark.asyncio
async def test_extract_session_marks_and_skips_when_disabled(
    users_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noesis.services.memory.user_settings import MemoryUserSettings

    monkeypatch.setattr(MemoryUserSettings, "is_enabled", staticmethod(lambda _u: False))
    db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    assert await extraction.MemoryExtractionService.extract_session(
        db, session_id="sess-off", user_id="u1"
    )
    assert db.commit.await_count == 1


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
