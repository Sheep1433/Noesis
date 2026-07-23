"""skills.sh 市场客户端与安装服务测试。"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from config import user_data_paths as paths
from exceptions.exception import ConflictException, NotFoundException, ServiceException
from services.skill_fs_service import SkillFsService
from services.skill_market_service import SkillMarketService
from services import skills_sh_client as client_mod
from services.skills_sh_client import SkillsShClient, validate_skill_id, validate_source


@pytest.fixture(autouse=True)
def clear_search_cache() -> None:
    client_mod._search_cache.clear()
    client_mod._leaderboard_cache.clear()
    client_mod._preview_cache.clear()


@pytest.fixture
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "users"
    monkeypatch.setattr(paths, "_USERS_ROOT", root)
    return root


def _make_repo_zip(skill_id: str = "demo-skill", *, layout: str = "skills") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if layout == "skills":
            prefix = f"repo-main/skills/{skill_id}"
        elif layout == "tools":
            prefix = f"repo-main/tools/video/{skill_id}"
        elif layout == "claude":
            prefix = f"repo-main/.claude/skills/{skill_id}"
        else:
            raise ValueError(layout)
        zf.writestr(
            f"{prefix}/SKILL.md",
            "---\nname: demo-skill\ndescription: demo\n---\n\n# Demo\n",
        )
        zf.writestr(f"{prefix}/notes.txt", "hi\n")
    return buf.getvalue()


def test_validate_source_and_skill_id() -> None:
    assert validate_source("anthropics/skills") == "anthropics/skills"
    assert validate_skill_id("pdf") == "pdf"
    with pytest.raises(ServiceException):
        validate_source("../etc/passwd")
    with pytest.raises(ServiceException):
        validate_skill_id("../x")


def test_find_skill_dir_layouts(tmp_path: Path) -> None:
    nested = tmp_path / "skills" / "pdf"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("# pdf", encoding="utf-8")
    assert SkillsShClient.find_skill_dir(tmp_path, "pdf") == nested

    flat = tmp_path / "flat-root"
    flat.mkdir()
    skill = flat / "xlsx"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# x", encoding="utf-8")
    assert SkillsShClient.find_skill_dir(flat, "xlsx") == skill

    single = tmp_path / "single"
    single.mkdir()
    (single / "SKILL.md").write_text("# root", encoding="utf-8")
    assert SkillsShClient.find_skill_dir(single, "anything") == single

    root_skill = tmp_path / "web-access-main"
    root_skill.mkdir()
    (root_skill / "SKILL.md").write_text("# web-access", encoding="utf-8")
    assert SkillsShClient.find_skill_dir(root_skill, "web-access") == root_skill

    deep = tmp_path / "tools" / "video" / "ai-video-generation"
    deep.mkdir(parents=True)
    (deep / "SKILL.md").write_text("# video", encoding="utf-8")
    assert SkillsShClient.find_skill_dir(tmp_path, "ai-video-generation") == deep

    claude = tmp_path / ".claude" / "skills" / "design-guide"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("# design", encoding="utf-8")
    assert SkillsShClient.find_skill_dir(tmp_path, "design-guide") == claude


def test_search_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "skills": [
            {
                "id": "anthropics/skills/pdf",
                "skillId": "pdf",
                "name": "pdf",
                "installs": 100,
                "source": "anthropics/skills",
            }
        ]
    }

    class FakeResp:
        status_code = 200
        is_error = False
        headers: dict = {}

        def json(self):
            return payload

    def fake_request(cls, method, url, **kwargs):
        assert method == "GET"
        assert "/api/search" in url
        assert kwargs["params"]["q"] == "pdf"
        return FakeResp()

    monkeypatch.setattr(SkillsShClient, "_request_skills_sh", classmethod(fake_request))
    hits = SkillsShClient.search("pdf", limit=5)
    assert len(hits) == 1
    assert hits[0].skill_id == "pdf"
    assert hits[0].installs == 100


def test_install_from_market(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _make_repo_zip("demo-skill")

    def fake_download(owner: str, repo: str):
        assert owner == "acme"
        assert repo == "skills"
        return archive, "main"

    monkeypatch.setattr(SkillsShClient, "_download_github_archive", staticmethod(fake_download))

    msg = SkillMarketService.install("u1", "acme/skills", "demo-skill")
    assert "demo-skill" in msg
    dest = paths.get_user_skills_dir("u1") / "demo-skill"
    assert (dest / "SKILL.md").is_file()
    origin = dest / ".skills-sh" / "origin.json"
    assert origin.is_file()
    data = json.loads(origin.read_text(encoding="utf-8"))
    assert data["source"] == "acme/skills"
    assert data["skillId"] == "demo-skill"


def test_install_deep_layout(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _make_repo_zip("ai-video-generation", layout="tools")

    monkeypatch.setattr(
        SkillsShClient,
        "_download_github_archive",
        staticmethod(lambda o, r: (archive, "main")),
    )

    msg = SkillMarketService.install("u1", "101-skills/skills", "ai-video-generation")
    assert "ai-video-generation" in msg
    assert (paths.get_user_skills_dir("u1") / "ai-video-generation" / "SKILL.md").is_file()


def test_install_missing_skill_raises_not_found(
    users_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _make_repo_zip("other-skill")

    monkeypatch.setattr(
        SkillsShClient,
        "_download_github_archive",
        staticmethod(lambda o, r: (archive, "main")),
    )

    with pytest.raises(NotFoundException):
        SkillMarketService.install("u1", "acme/skills", "demo-skill")


def test_install_conflict_without_overwrite(
    users_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = paths.ensure_user_skills_dir("u1") / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# exists", encoding="utf-8")

    monkeypatch.setattr(
        SkillsShClient,
        "_download_github_archive",
        staticmethod(lambda o, r: (_make_repo_zip("demo-skill"), "main")),
    )

    with pytest.raises(ConflictException):
        SkillMarketService.install("u1", "acme/skills", "demo-skill", overwrite=False)


def test_install_package_dir_overwrite(users_root: Path) -> None:
    dest_parent = paths.ensure_user_skills_dir("u1")
    old = dest_parent / "pack"
    old.mkdir()
    (old / "SKILL.md").write_text("old", encoding="utf-8")

    src = users_root / "src-pack"
    src.mkdir()
    (src / "SKILL.md").write_text("new", encoding="utf-8")

    ok, _ = SkillFsService.install_package_dir(str(src), "pack", "u1", overwrite=True)
    assert ok is True
    assert (dest_parent / "pack" / "SKILL.md").read_text(encoding="utf-8") == "new"


def test_parse_installs_text() -> None:
    assert client_mod.parse_installs_text("2.6M") == 2_600_000
    assert client_mod.parse_installs_text("687.4K") == 687_400
    assert client_mod.parse_installs_text("42") == 42


def test_parse_leaderboard_html() -> None:
    html = """
    <a href="/vercel-labs/skills/find-skills" class="x">
      <h3 class="font-semibold text-foreground truncate whitespace-nowrap">find-skills</h3>
      <span class="font-mono text-sm text-foreground">2.6M</span>
    </a>
    <a href="/anthropics/skills/frontend-design" class="x">
      <h3 class="font-semibold text-foreground truncate whitespace-nowrap">frontend-design</h3>
      <span class="font-mono text-sm text-foreground">687.4K</span>
    </a>
    <a href="/site/open.feishu.cn/lark-approval" class="x">
      <h3>lark-approval</h3>
      <span class="font-mono text-sm text-foreground">100K</span>
    </a>
    """
    hits = SkillsShClient._parse_leaderboard_html(html)
    assert len(hits) == 2
    assert hits[0].skill_id == "find-skills"
    assert hits[0].installs == 2_600_000
    assert hits[1].source == "anthropics/skills"


def test_browse_trending(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_lb(sort: str = "trending", *, limit: int = 40):
        assert sort == "trending"
        return [
            client_mod.SkillsShSearchHit(
                id="101-skills/skills/ai-video-generation",
                skill_id="ai-video-generation",
                name="ai-video-generation",
                source="101-skills/skills",
                installs=21_500,
            ),
        ][:limit]

    monkeypatch.setattr(SkillsShClient, "fetch_leaderboard", staticmethod(fake_lb))
    resp = SkillMarketService.browse("u1", sort="trending", limit=10)
    assert len(resp.items) == 1
    assert resp.items[0].skill_id == "ai-video-generation"
    assert resp.total == 1


def test_browse_pagination(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_lb(sort: str = "trending", *, limit: int = 40):
        return [
            client_mod.SkillsShSearchHit(
                id=f"owner/repo/skill-{i}",
                skill_id=f"skill-{i}",
                name=f"skill-{i}",
                source="owner/repo",
                installs=i,
            )
            for i in range(5)
        ][:limit]

    monkeypatch.setattr(SkillsShClient, "fetch_leaderboard", staticmethod(fake_lb))
    resp = SkillMarketService.browse("u1", sort="trending", limit=2, offset=2)
    assert resp.total == 5
    assert len(resp.items) == 2
    assert resp.items[0].skill_id == "skill-2"


def test_browse_all_time(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_lb(sort: str = "all_time", *, limit: int = 40):
        assert sort == "all_time"
        return [
            client_mod.SkillsShSearchHit(
                id="vercel-labs/skills/find-skills",
                skill_id="find-skills",
                name="find-skills",
                source="vercel-labs/skills",
                installs=2_600_000,
            ),
        ][:limit]

    monkeypatch.setattr(SkillsShClient, "fetch_leaderboard", staticmethod(fake_lb))
    resp = SkillMarketService.browse("u1", sort="all_time", limit=10)
    assert resp.items[0].installs == 2_600_000


def test_browse_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(sort: str = "trending", *, limit: int = 40):
        raise ServiceException(message="leaderboard down")

    monkeypatch.setattr(SkillsShClient, "fetch_leaderboard", staticmethod(boom))
    with pytest.raises(ServiceException) as ei:
        SkillMarketService.browse("u1", sort="trending")
    assert ei.value.message == "leaderboard down"


def test_leaderboard_stale_cache_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    hit = client_mod.SkillsShSearchHit(
        id="acme/skills/demo",
        skill_id="demo",
        name="demo",
        source="acme/skills",
        installs=1,
    )
    client_mod._leaderboard_cache["trending"] = (time.monotonic() - 600, [hit])

    def boom(cls, method, url, **kwargs):
        raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]")

    monkeypatch.setattr(SkillsShClient, "_request_skills_sh", classmethod(boom))
    results = SkillsShClient.fetch_leaderboard("trending", limit=10)
    assert len(results) == 1
    assert results[0].skill_id == "demo"


def test_request_skills_sh_retries_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class FakeResp:
        status_code = 200
        text = "ok"
        is_error = False
        headers: dict = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise httpx.ConnectError("transient")
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    resp = SkillsShClient._request_skills_sh("GET", "https://skills.sh/trending")
    assert resp.status_code == 200
    assert len(calls) == 3


def test_annotate_exact_and_name_conflict(users_root: Path) -> None:
    exact = paths.ensure_user_skills_dir("u1") / "pdf"
    exact.mkdir(parents=True)
    (exact / "SKILL.md").write_text("# pdf", encoding="utf-8")
    meta = exact / ".skills-sh"
    meta.mkdir()
    (meta / "origin.json").write_text(
        json.dumps({"source": "anthropics/skills", "skillId": "pdf"}),
        encoding="utf-8",
    )

    conflict = paths.ensure_user_skills_dir("u1") / "docx"
    conflict.mkdir()
    (conflict / "SKILL.md").write_text("# zip upload", encoding="utf-8")

    items = [
        SkillMarketService._to_item(
            client_mod.SkillsShSearchHit(
                id="anthropics/skills/pdf",
                skill_id="pdf",
                name="pdf",
                source="anthropics/skills",
                installs=1,
            ),
        ),
        SkillMarketService._to_item(
            client_mod.SkillsShSearchHit(
                id="other/skills/pdf",
                skill_id="pdf",
                name="pdf",
                source="other/skills",
                installs=2,
            ),
        ),
        SkillMarketService._to_item(
            client_mod.SkillsShSearchHit(
                id="anthropics/skills/docx",
                skill_id="docx",
                name="docx",
                source="anthropics/skills",
                installs=3,
            ),
        ),
    ]
    SkillMarketService._annotate_install_status(items, "u1")
    assert items[0].install_match == "exact"
    assert items[0].installed is True
    assert items[1].install_match == "name_conflict"
    assert items[1].installed is False
    assert items[2].install_match == "name_conflict"
    assert items[2].installed is False


def test_pick_skill_md_from_download() -> None:
    skill_md, relpath = SkillsShClient._pick_skill_md_from_download([
        {"path": "references/note.md", "contents": "x"},
        {"path": ".claude/skills/design-guide/SKILL.md", "contents": "deep"},
        {"path": "SKILL.md", "contents": "---\nname: demo\n---\n\n# Demo\n"},
    ])
    assert relpath == "SKILL.md"
    assert "# Demo" in skill_md


def test_parse_skill_md_display_name() -> None:
    md = "---\nname: design-guide\ndescription: x\n---\n\n# Guide\n"
    assert SkillsShClient._parse_skill_md_display_name(md) == "design-guide"
    assert SkillsShClient._parse_skill_md_display_name("# no frontmatter") is None


def test_fetch_skill_preview_from_download_api(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "files": [
            {
                "path": "SKILL.md",
                "contents": (
                    "---\nname: design-guide\n---\n\n"
                    "# Paperclip Design Guide\n\n## 13. Common Mistakes to Avoid\n"
                ),
            },
            {"path": "scripts/run.py", "contents": "print('hi')\n"},
        ],
        "hash": "abc",
    }

    class FakeResp:
        status_code = 200
        is_error = False
        headers: dict = {}

        def json(self):
            return payload

    def fake_request(cls, method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/api/download/getpaperclipai/paperclip/design-guide")
        return FakeResp()

    monkeypatch.setattr(SkillsShClient, "_request_skills_sh", classmethod(fake_request))
    preview = SkillsShClient.fetch_skill_preview("getpaperclipai/paperclip", "design-guide")
    assert "Paperclip Design Guide" in preview.skill_md
    assert "13. Common Mistakes" in preview.skill_md
    assert preview.skill_md_relpath == "SKILL.md"
    assert preview.tree == []
    assert preview.display_name == "design-guide"
