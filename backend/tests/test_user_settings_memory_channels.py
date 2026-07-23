"""用户记忆 API / 路径 / Agent 不可达 channels。"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.backends.memory import UserMemoryBackend
from config.user_data_paths import (
    ensure_user_memory_files,
    get_user_channels_path,
    get_user_daily_memory_path,
    get_user_memory_dir,
)
from services.messaging_channel_service import MessagingChannelService
from services.user_memory_service import UserMemoryService


def test_user_memory_read_write_same_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.user_data_paths._USERS_ROOT", tmp_path / "users")
    uid = "u-mem-1"
    ensure_user_memory_files(uid)
    written = UserMemoryService.write_file(uid, "USER.md", "# hello\n")
    assert "hello" in written["content"]
    read = UserMemoryService.read_file(uid, "USER.md")
    assert read["content"] == "# hello\n"
    assert read["updated_at"]


def test_user_memory_rejects_illegal_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.user_data_paths._USERS_ROOT", tmp_path / "users")
    with pytest.raises(ValueError, match="非法记忆文件名"):
        UserMemoryService.read_file("u1", "channels.json")


def test_daily_memory_path_and_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.user_data_paths._USERS_ROOT", tmp_path / "users")
    uid = "u-l2"
    ensure_user_memory_files(uid)
    assert get_user_memory_dir(uid).is_dir()
    p = get_user_daily_memory_path(uid, "2026-07-23")
    assert p.name == "2026-07-23.md"
    with pytest.raises(ValueError):
        get_user_daily_memory_path(uid, "bad")


def test_agent_memory_backend_cannot_write_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.user_data_paths._USERS_ROOT", tmp_path / "users")
    uid = "u-ch"
    ensure_user_memory_files(uid)
    agents = tmp_path / "users" / uid / "AGENTS.md"
    user = tmp_path / "users" / uid / "USER.md"
    backend = UserMemoryBackend(agents_path=agents, user_path=user)
    result = backend.write("channels.json", '{"x":1}')
    assert result.error
    # 通道文件本身也不在 memory 白名单路径下
    ch_path = get_user_channels_path(uid)
    assert not ch_path.is_file() or "channels" in str(ch_path)


def test_channels_token_masked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.user_data_paths._USERS_ROOT", tmp_path / "users")
    uid = "u-tg"
    item = MessagingChannelService.create_channel(
        uid,
        {
            "type": "telegram",
            "display_name": "Bot",
            "bot_token": "123456:ABCDEFGHsecret",
            "pairing_chat_id": "999",
            "enabled": True,
        },
    )
    assert item["bot_token_masked"].startswith("****")
    assert "ABCDEF" not in (item["bot_token_masked"] or "")
    assert item["has_token"] is True
    listed = MessagingChannelService.list_channels(uid)
    assert len(listed) == 1
    # session id 须适配 t_chat_session.id VARCHAR(36)
    assert listed[0]["default_session_id"]
    assert len(listed[0]["default_session_id"]) <= 36
    assert listed[0]["default_session_id"] == item["channel_id"]
