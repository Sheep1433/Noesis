from pathlib import Path

import pytest

from noesis.config.user_data_paths import ensure_user_memory_files, get_user_daily_memory_path
from noesis.services.memory_dream_service import DreamMessage, build_entries, parse_daily_entries, render_daily_memory
from noesis.services.user_memory_service import UserMemoryService


@pytest.fixture(autouse=True)
def user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")


def test_build_entries_is_idempotent_and_ignores_unpaired_messages() -> None:
    messages = [
        DreamMessage("u1", "s1", "user", "决定采用 LangGraph", 1),
        DreamMessage("a1", "s1", "assistant", "已记录方案", 2),
        DreamMessage("a2", "s1", "assistant", "孤立回复", 3),
    ]
    first = build_entries("owner", messages)
    second = build_entries("owner", messages)
    assert first == second
    assert len(first) == 1
    assert first[0]["category"] == "decision"
    assert first[0]["sources"][0] == {"session_id": "s1", "message_id": "u1"}


def test_render_parse_and_search_entries_are_user_scoped() -> None:
    ensure_user_memory_files("u1")
    ensure_user_memory_files("u2")
    entries = build_entries("u1", [
        DreamMessage("u1", "s1", "user", "记住蓝色按钮", 1),
        DreamMessage("a1", "s1", "assistant", "偏好已确认", 2),
    ])
    content = render_daily_memory("2026-07-27", "Asia/Shanghai", entries)
    get_user_daily_memory_path("u1", "2026-07-27").write_text(content, encoding="utf-8")
    get_user_daily_memory_path("u2", "2026-07-27").write_text(content.replace("蓝色", "红色"), encoding="utf-8")

    parsed = parse_daily_entries(content, "2026-07-27")
    assert parsed[0]["id"] == entries[0]["id"]
    assert UserMemoryService.search_entries("u1", "蓝色")[0]["date"] == "2026-07-27"
    assert UserMemoryService.search_entries("u1", "红色") == []


def test_search_entries_validates_date_range() -> None:
    ensure_user_memory_files("u1")
    with pytest.raises(ValueError, match="开始日期"):
        UserMemoryService.search_entries("u1", "test", date_from="2026-07-28", date_to="2026-07-27")
