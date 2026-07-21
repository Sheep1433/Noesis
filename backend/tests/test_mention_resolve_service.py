"""MentionResolveService 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from exceptions.exception import ServiceException
from schemas.qa_vo import MentionItem
from services.mention_resolve_service import MentionResolveService, parse_mention_items


@pytest.fixture()
def user_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    uid = "u1"
    sid = "s1"
    root = tmp_path / "users" / uid
    workspace = root / "sessions" / sid / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "notes.md").write_text("# hello\n", encoding="utf-8")
    (workspace / "nested").mkdir()
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    user_skills = root / "skills" / "demo-skill"
    user_skills.mkdir(parents=True)
    (user_skills / "SKILL.md").write_text("# demo\n", encoding="utf-8")

    platform = tmp_path / "platform-skills" / "deep-research-v2"
    platform.mkdir(parents=True)
    (platform / "SKILL.md").write_text("# dr\n", encoding="utf-8")

    monkeypatch.setattr(
        "services.mention_resolve_service.get_user_root",
        lambda user_id: root if str(user_id) == uid else tmp_path / "users" / str(user_id),
    )
    monkeypatch.setattr(
        "services.mention_resolve_service.get_workspace_dir",
        lambda user_id, session_id: root / "sessions" / session_id / "workspace",
    )
    monkeypatch.setattr(
        "services.mention_resolve_service.get_session_uploads_dir",
        lambda user_id, session_id: root / "sessions" / session_id / "uploads",
    )
    monkeypatch.setattr(
        "services.mention_resolve_service.get_session_attachments_dir",
        lambda user_id, session_id: root / "sessions" / session_id / "attachments",
    )
    monkeypatch.setattr(
        "services.mention_resolve_service.get_user_skills_dir",
        lambda user_id: root / "skills",
    )
    monkeypatch.setattr(
        "services.mention_resolve_service.get_user_agents_md_path",
        lambda user_id: root / "AGENTS.md",
    )
    monkeypatch.setattr(
        "services.mention_resolve_service.get_user_profile_md_path",
        lambda user_id: root / "USER.md",
    )
    monkeypatch.setattr(
        "services.mention_resolve_service.skills_root",
        lambda: tmp_path / "platform-skills",
    )
    return uid, sid, root


def test_omit_mentions_returns_empty(user_session):
    uid, sid, _ = user_session
    resolved = MentionResolveService.resolve(
        mentions=None,
        qa_type="SUPER_AGENT_QA",
        user_id=uid,
        session_id=sid,
    )
    assert resolved.prompt_block == ""
    assert resolved.skill_ids == []
    assert resolved.persistence == []


def test_skill_and_file_and_subagent(user_session):
    uid, sid, _ = user_session
    resolved = MentionResolveService.resolve(
        mentions=[
            MentionItem(type="skill", id="deep-research-v2", source="platform"),
            MentionItem(type="file", path=f"sessions/{sid}/workspace/notes.md"),
            MentionItem(type="subagent", id="task-worker"),
        ],
        qa_type="SUPER_AGENT_QA",
        user_id=uid,
        session_id=sid,
    )
    assert "deep-research-v2" in resolved.skill_ids
    assert "/skills/public/deep-research-v2/SKILL.md" in resolved.prompt_block
    assert "`/notes.md`" in resolved.prompt_block
    assert "task-worker" in resolved.prompt_block
    assert len(resolved.persistence) == 3


def test_path_traversal_rejected(user_session):
    uid, sid, _ = user_session
    with pytest.raises(ServiceException) as ei:
        MentionResolveService.resolve(
            mentions=[MentionItem(type="file", path="../etc/passwd")],
            qa_type="SUPER_AGENT_QA",
            user_id=uid,
            session_id=sid,
        )
    assert "非法路径" in (ei.value.message or "")


def test_cross_session_path_rejected(user_session):
    uid, sid, _ = user_session
    with pytest.raises(ServiceException) as ei:
        MentionResolveService.resolve(
            mentions=[MentionItem(type="file", path="sessions/other/workspace/notes.md")],
            qa_type="SUPER_AGENT_QA",
            user_id=uid,
            session_id=sid,
        )
    assert "非法路径" in (ei.value.message or "")


def test_fault_rejects_skill(user_session):
    uid, sid, _ = user_session
    with pytest.raises(ServiceException) as ei:
        MentionResolveService.resolve(
            mentions=[MentionItem(type="skill", id="demo-skill", source="user")],
            qa_type="FAULT_OPERATION_QA",
            user_id=uid,
            session_id=sid,
        )
    assert "skill" in (ei.value.message or "").lower()


def test_fault_accepts_file_and_subagent(user_session):
    uid, sid, _ = user_session
    resolved = MentionResolveService.resolve(
        mentions=[
            MentionItem(type="file", path="workspace/notes.md"),
            MentionItem(type="subagent", id="general-purpose"),
        ],
        qa_type="FAULT_OPERATION_QA",
        user_id=uid,
        session_id=sid,
    )
    assert "`/notes.md`" in resolved.prompt_block
    assert "general-purpose" in resolved.prompt_block


def test_unknown_subagent_rejected(user_session):
    uid, sid, _ = user_session
    with pytest.raises(ServiceException) as ei:
        MentionResolveService.resolve(
            mentions=[MentionItem(type="subagent", id="no-such")],
            qa_type="SUPER_AGENT_QA",
            user_id=uid,
            session_id=sid,
        )
    assert "subagent" in (ei.value.message or "")


def test_common_qa_rejects_mentions(user_session):
    uid, sid, _ = user_session
    with pytest.raises(ServiceException) as ei:
        MentionResolveService.resolve(
            mentions=[MentionItem(type="file", path="workspace/notes.md")],
            qa_type="COMMON_QA",
            user_id=uid,
            session_id=sid,
        )
    assert "mentions" in (ei.value.message or "")


def test_parse_mention_items_invalid():
    with pytest.raises(ServiceException) as ei:
        parse_mention_items({"type": "skill"})
    assert "数组" in (ei.value.message or "")
