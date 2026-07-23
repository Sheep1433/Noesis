"""会话上下文 API 服务测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from config import user_data_paths as paths
from services.session_context_service import SessionContextService


@pytest.mark.asyncio
async def test_read_workspace_file_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    ws = paths.ensure_workspace_dir(uid, sid)
    (ws / "ok.md").write_text("hello", encoding="utf-8")
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        rel, content = await SessionContextService.read_workspace_file(
            sid, uid, 'sessions/s1/workspace/ok.md', db,
        )
        assert rel == 'sessions/s1/workspace/ok.md'
        assert content == "hello"

        with pytest.raises(HTTPException) as exc_legacy:
            await SessionContextService.read_workspace_file(
                sid, uid, 'workspace/ok.md', db,
            )
        assert exc_legacy.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            await SessionContextService.read_workspace_file(sid, uid, "../etc/passwd", db)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_read_user_root_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    paths.ensure_user_memory_files(uid)
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        rel, content = await SessionContextService.read_workspace_file(
            sid, uid, 'AGENTS.md', db,
        )
        assert rel == 'AGENTS.md'
        assert 'Noesis' in content


@pytest.mark.asyncio
async def test_get_context_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    paths.ensure_user_memory_files(uid)
    ws = paths.ensure_workspace_dir(uid, sid)
    (ws / "report.md").write_text("# hi", encoding="utf-8")
    skills = paths.ensure_user_skills_dir(uid)
    skill_dir = skills / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("skill", encoding="utf-8")
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        ctx = await SessionContextService.get_context(sid, uid, db)
    assert len(ctx.tree) == 1
    root = ctx.tree[0]
    assert root.label == 'users/u1'
    labels = [node.label for node in root.children or []]
    assert 'AGENTS.md' in labels
    assert 'USER.md' in labels
    assert 'skills' in labels
    assert f'sessions/{sid}' in labels
    session_node = next(n for n in root.children or [] if n.label == f'sessions/{sid}')
    assert session_node.children and [node.label for node in session_node.children] == ['workspace']
    ws_node = session_node.children[0]
    assert ws_node.children and ws_node.children[0].key == f'sessions/{sid}/workspace/report.md'


@pytest.mark.asyncio
async def test_get_context_hides_empty_workspace_and_attachments_dup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    uploads = paths.ensure_session_uploads_dir(uid, sid)
    (uploads / "resume.md").write_text("raw", encoding="utf-8")
    attachments = paths.ensure_session_attachments_dir(uid, sid)
    (attachments / "resume.md").write_text("parsed", encoding="utf-8")
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        ctx = await SessionContextService.get_context(sid, uid, db)
    root = ctx.tree[0]
    assert root.label == 'users/u1'
    session_node = next(n for n in root.children or [] if n.label == f'sessions/{sid}')
    assert session_node.children and [node.label for node in session_node.children] == ['uploads']
    assert session_node.children[0].children and session_node.children[0].children[0].key == f'sessions/{sid}/uploads/resume.md'


@pytest.mark.asyncio
async def test_get_context_not_owned() -> None:
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(HTTPException) as exc:
            await SessionContextService.get_context("s1", "u1", db)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_write_user_memory_files_via_panel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    paths.ensure_user_memory_files(uid)
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        rel, content = await SessionContextService.write_workspace_file(
            sid, uid, 'USER.md', '# 用户画像\n\n研发', db,
        )
        assert rel == 'USER.md'
        assert content == '# 用户画像\n\n研发'
        assert paths.get_user_profile_md_path(uid).read_text(encoding="utf-8") == '# 用户画像\n\n研发'

        rel_agents, content_agents = await SessionContextService.write_workspace_file(
            sid, uid, 'AGENTS.md', '## 偏好\n中文', db,
        )
        assert rel_agents == 'AGENTS.md'
        assert paths.get_user_agents_md_path(uid).read_text(encoding="utf-8") == '## 偏好\n中文'


@pytest.mark.asyncio
async def test_write_workspace_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    ws = paths.ensure_workspace_dir(uid, sid)
    (ws / "draft.md").write_text("old", encoding="utf-8")
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        rel, content = await SessionContextService.write_workspace_file(
            sid, uid, f'sessions/{sid}/workspace/draft.md', '# updated', db,
        )
        assert rel == f'sessions/{sid}/workspace/draft.md'
        assert content == '# updated'
        assert (ws / "draft.md").read_text(encoding="utf-8") == '# updated'

        with pytest.raises(HTTPException) as exc:
            await SessionContextService.write_workspace_file(
                sid, uid, '../etc/passwd', 'hack', db,
            )
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as missing_exc:
            await SessionContextService.write_workspace_file(
                sid, uid, f'sessions/{sid}/workspace/missing.md', 'x', db,
            )
        assert missing_exc.value.status_code == 404


@pytest.mark.asyncio
async def test_build_path_archive_workspace_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    ws = paths.ensure_workspace_dir(uid, sid)
    (ws / "a.md").write_text("hello", encoding="utf-8")
    sub = ws / "nested"
    sub.mkdir()
    (sub / "b.txt").write_text("world", encoding="utf-8")
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        name, data = await SessionContextService.build_path_archive(
            sid, uid, f'sessions/{sid}/workspace', db,
        )
        assert name == 'workspace.zip'
        assert data[:2] == b'PK'
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = sorted(zf.namelist())
            assert names == ['a.md', 'nested/b.txt']
            assert zf.read('a.md') == b'hello'


@pytest.mark.asyncio
async def test_build_path_archive_skills_subdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    skills = paths.ensure_user_skills_dir(uid)
    pkg = skills / "demo-skill"
    pkg.mkdir()
    (pkg / "SKILL.md").write_text("skill", encoding="utf-8")
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        name, data = await SessionContextService.build_path_archive(
            sid, uid, 'skills/demo-skill', db,
        )
        assert name == 'demo-skill.zip'
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.namelist() == ['SKILL.md']


@pytest.mark.asyncio
async def test_build_path_archive_rejects_user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        with pytest.raises(HTTPException) as exc:
            await SessionContextService.build_path_archive(sid, uid, f'users/{uid}', db)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_download_workspace_path_single_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    ws = paths.ensure_workspace_dir(uid, sid)
    (ws / "note.md").write_text("# hi", encoding="utf-8")
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        name, data, media_type = await SessionContextService.download_workspace_path(
            sid, uid, f'sessions/{sid}/workspace/note.md', db,
        )
        assert name == 'note.md'
        assert data == b'# hi'
        assert media_type
        assert data[:2] != b'PK'


@pytest.mark.asyncio
async def test_build_path_archive_old_file_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_USERS_ROOT", tmp_path / "users")
    uid, sid = "u1", "s1"
    ws = paths.ensure_workspace_dir(uid, sid)
    old_file = ws / "legacy.txt"
    old_file.write_text("old", encoding="utf-8")
    import os
    os.utime(old_file, (0, 0))
    db = AsyncMock()
    with patch(
        "services.session_context_service.ChatService.get_session_by_id",
        new_callable=AsyncMock,
        return_value=object(),
    ):
        name, data = await SessionContextService.build_path_archive(
            sid, uid, f'sessions/{sid}/workspace', db,
        )
        assert name == 'workspace.zip'
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.read('legacy.txt') == b'old'
