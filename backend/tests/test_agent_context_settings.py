"""Raw profile and side-effect-free context preview."""

from pathlib import Path

import pytest

from noesis.agents.context import ContextResolver
from noesis.config.user_data_paths import ensure_user_memory_files, get_user_profile_md_path
from noesis.services.user_memory_service import UserMemoryService


@pytest.fixture(autouse=True)
def user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")


def test_profile_is_raw_markdown() -> None:
    ensure_user_memory_files("u1")
    path = get_user_profile_md_path("u1")
    original = path.read_text(encoding="utf-8") + "\n## 自定义\n保留这段内容\n"
    path.write_text(original, encoding="utf-8")

    result = UserMemoryService.write_file("u1", "USER.md", original)

    assert "## 自定义\n保留这段内容" in result["content"]
    assert "profile" not in result


def test_profile_accepts_custom_raw_document() -> None:
    ensure_user_memory_files("u1")
    get_user_profile_md_path("u1").write_text("# 完全自定义画像\n", encoding="utf-8")

    result = UserMemoryService.write_file("u1", "USER.md", "# 完全自定义画像\n")
    assert result["content"] == "# 完全自定义画像\n"


def test_context_preview_uses_runtime_resolver_without_side_effects(tmp_path: Path) -> None:
    ensure_user_memory_files("u1")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    resolved = ContextResolver.resolve("u1", "super_agent")
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    assert resolved.memory_sources == (
        "/memory/USER.md",
        "/memory/AGENTS.md",
        "/memory/MEMORY.md",
    )
    assert [source.id for source in resolved.sources] == [
        "system",
        "profile",
        "memory",
        "memory-index",
    ]
    assert "用户画像" in resolved.compiled_content
    assert before == after


def test_non_memory_profile_does_not_inject_user_files() -> None:
    resolved = ContextResolver.resolve("u1", "common_qa")
    assert resolved.memory_sources == ()
    assert [source.id for source in resolved.sources] == ["system"]
