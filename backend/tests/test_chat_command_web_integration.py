"""Web 通道 create_run 命令拦截：命中则不建 run、不落库，返回 command_reply。"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from noesis.chat.commands import registry as reg


@pytest.fixture(autouse=True)
def _ensure_handlers() -> None:
    import noesis.chat.commands.handlers  # noqa: F401 —— 触发 @command 注册


async def test_web_command_reply_does_not_create_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import server.api.chat_api as api

    monkeypatch.setattr(api.pg_manager, "_advisory_lock_ready", True)
    create_mock = AsyncMock()
    monkeypatch.setattr(api.RunService, "create", create_mock)

    from noesis.schemas.chat_vo import CreateRunRequest

    req = CreateRunRequest(session_id="s1", content="/help", client_request_id="abcdefgh")
    user = SimpleNamespace(user_id=1)

    resp = await api.create_run(req, user, db=None)

    assert resp.status_code == 200
    data = json.loads(resp.body)["data"]
    assert data["command_reply"]
    assert "/help" in data["command_reply"]
    assert data["session_id"] == "s1"
    # 关键：未命中 Agent / 未建 run
    create_mock.assert_not_awaited()


async def test_web_non_command_proceeds_to_create_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import server.api.chat_api as api

    monkeypatch.setattr(api.pg_manager, "_advisory_lock_ready", True)
    created_run = SimpleNamespace(
        id="r-1", assistant_message_id="m-1", session_id="s1", status="queued"
    )
    create_mock = AsyncMock(return_value=created_run)
    monkeypatch.setattr(api.RunService, "create", create_mock)
    monkeypatch.setattr(
        api.ChatService, "get_session_by_id",
        AsyncMock(return_value=SimpleNamespace(title="t")),
    )

    from noesis.schemas.chat_vo import CreateRunRequest

    req = CreateRunRequest(
        session_id="s1", content="帮我查天气", client_request_id="abcdefgh"
    )
    resp = await api.create_run(req, SimpleNamespace(user_id=1), db=None)

    create_mock.assert_awaited_once()
    data = json.loads(resp.body)["data"]
    assert data["run_id"] == "r-1"
    assert "command_reply" not in data


async def test_list_commands_endpoint_returns_descriptions() -> None:
    """GET /commands 返回控制命令 name + description。"""
    import server.api.chat_api as api

    resp = await api.list_commands(SimpleNamespace(user_id=1))
    assert resp.status_code == 200
    items = json.loads(resp.body)["data"]
    names = [it["name"] for it in items]
    assert "help" in names
    assert "skills" in names
    help_item = next(it for it in items if it["name"] == "help")
    assert help_item["description"]


async def test_web_skill_command_rewrites_then_creates_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Web /skill-name <问题> → dispatch 返回 rewrite → 改写 content+skills 走 RunService.create。"""
    import server.api.chat_api as api

    monkeypatch.setattr(api.pg_manager, "_advisory_lock_ready", True)

    captured: dict = {}
    created_run = SimpleNamespace(
        id="r-2", assistant_message_id="m-2", session_id="s1", status="queued"
    )

    async def fake_create(request, current_user, db):
        captured["content"] = request.content
        captured["extra"] = request.extra
        return created_run

    monkeypatch.setattr(api.RunService, "create", fake_create)
    monkeypatch.setattr(
        api.ChatService, "get_session_by_id",
        AsyncMock(return_value=SimpleNamespace(title="t")),
    )

    from noesis.schemas.chat_vo import CreateRunRequest

    req = CreateRunRequest(
        session_id="s1",
        content="/baoyu-url-to-markdown 抓取 https://example.com",
        client_request_id="abcdefgh",
    )
    resp = await api.create_run(req, SimpleNamespace(user_id=1), db=None)

    # 走了正常 create（不是 command_reply）
    data = json.loads(resp.body)["data"]
    assert data["run_id"] == "r-2"
    assert "command_reply" not in data
    # content 被改写为用户问题，enabled_skills 注入 skill
    assert captured["content"] == "抓取 https://example.com"
    assert captured["extra"]["enabled_skills"] == ["baoyu-url-to-markdown"]
