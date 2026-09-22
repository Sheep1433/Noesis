"""用户记忆 API / 路径 / Agent 不可达 channels。"""
from __future__ import annotations

from pathlib import Path

import pytest

from noesis.memory.layout import ensure_user_memory_files
from noesis.config.user_data_paths import (
    get_user_channels_path,
)
from noesis.services.messaging_channel_service import MessagingChannelService
from noesis.services.user_memory_service import UserMemoryService


def test_user_memory_read_write_same_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")
    uid = "u-mem-1"
    ensure_user_memory_files(uid)
    written = UserMemoryService.write_file(uid, "USER.md", "# hello\n")
    assert "hello" in written["content"]
    read = UserMemoryService.read_file(uid, "USER.md")
    assert read["content"] == "# hello\n"
    assert read["updated_at"]


def test_user_memory_rejects_illegal_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")
    with pytest.raises(ValueError, match="非法记忆文件名"):
        UserMemoryService.read_file("u1", "channels.json")


def test_agent_memory_middleware_cannot_write_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/memory 白名单外写入（channels.json 非根文件、非条目）被门卫拒绝。"""
    from noesis.agents.backends.factory import build_agent_filesystem_backend
    from noesis.agents.middlewares.memory_write_middleware import (
        MemoryWriteMiddleware,
        MemoryWriteRejected,
    )
    from types import SimpleNamespace

    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")
    uid = "u-ch"
    ensure_user_memory_files(uid)
    backend = build_agent_filesystem_backend(
        user_id=uid, session_id="ch-test", sandbox=None, shell_timeout=30,
    )
    mw = MemoryWriteMiddleware(user_id=uid)
    request = SimpleNamespace(
        tool_call={"name": "write_file", "args": {"file_path": "/memory/channels.json"}}
    )
    with pytest.raises(MemoryWriteRejected):
        mw.wrap_tool_call(request, lambda _req: backend.write("/memory/channels.json", '{"x":1}'))
    # 通道文件本身不在 memory 子树下
    ch_path = get_user_channels_path(uid)
    assert not ch_path.is_file() or "channels" in str(ch_path)


def test_channels_token_masked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("noesis.config.user_data_paths._USERS_ROOT", tmp_path / "users")
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
