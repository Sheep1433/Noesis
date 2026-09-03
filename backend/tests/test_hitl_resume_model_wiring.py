"""HITL resume 段与压缩摘要器的模型接线契约。

背景（2026-09-03 run bf9dfa40）：第一段（exec_query）正确用自定义模型
glm-5.3-flash，ask_user 触发 HITL 中断后用户批准续跑——resume 段经
``exec_hitl_resume`` 重建 Agent 时**不传 model_id**，``get_llm(None)`` 静默
落平台默认 kilo（model_id=None 绕过 strict 校验），续跑段全部调用漂移。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_pg_manager():
    """exec_hitl_resume 内部用 pg_manager 读既有消息内容——替换为空内容。"""
    mgmt = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    @asynccontextmanager
    async def ctx():
        db = MagicMock()
        db.execute = AsyncMock(return_value=result)
        yield db

    mgmt.get_async_session_context = ctx
    return mgmt


@pytest.mark.asyncio
async def test_hitl_resume_passes_frozen_model_to_agent(monkeypatch) -> None:
    """resume 段必须把 run 冻结模型传给 resume_agent 并重设运行时快照。"""
    from noesis.services.qa import service as qa_service

    async def fake_resolve(*, session_id, user_id, request_model_id, db):
        fake_resolve.calls = getattr(fake_resolve, "calls", 0) + 1
        assert request_model_id == "token/glm-5.3-flash"
        return "token/glm-5.3-flash"

    async def fake_resume_agent(**kwargs):
        fake_resume_agent.kwargs = kwargs
        return
        yield  # pragma: no cover - make it an async generator

    monkeypatch.setattr(
        "noesis.services.qa.helpers._resolve_model_for_query", fake_resolve
    )
    agent = MagicMock()
    agent.resume_agent = fake_resume_agent
    monkeypatch.setattr(qa_service, "super_agent", agent)
    monkeypatch.setattr(qa_service, "pg_manager", _fake_pg_manager())

    from noesis.schemas.login_vo import CurrentUser

    pending = MagicMock()
    pending.session_id = "session-1"
    pending.user_id = "user-1"
    pending.assistant_message_id = "msg-1"
    pending.expires_at = 0.0
    pending.action_requests = []
    pending.review_configs = []

    events = [
        event
        async for event in qa_service.QaService.exec_hitl_resume(
            pending=pending,
            decisions=[],
            grant_scope=None,
            current_user=CurrentUser(user_id="user-1", username="t"),
            db=MagicMock(),
            run_id="run-1",
            model_id="token/glm-5.3-flash",
        )
    ]

    assert fake_resolve.calls == 1, "resume 段必须解析冻结模型以重设快照"
    assert fake_resume_agent.kwargs.get("model_id") == "token/glm-5.3-flash"
    assert isinstance(events, list)  # 空 agent 生成器仍产出 message-start/finish 骨架帧


@pytest.mark.asyncio
async def test_hitl_resume_without_model_keeps_none(monkeypatch) -> None:
    """旧 run 无冻结模型（payload 缺 resolved_model）：保持 None，不额外解析。"""
    from noesis.services.qa import service as qa_service

    async def fake_resolve(**kwargs):
        raise AssertionError("model_id 为 None 时不应触发模型解析")

    async def fake_resume_agent(**kwargs):
        fake_resume_agent.kwargs = kwargs
        return
        yield  # pragma: no cover

    monkeypatch.setattr(
        "noesis.services.qa.helpers._resolve_model_for_query", fake_resolve
    )
    agent = MagicMock()
    agent.resume_agent = fake_resume_agent
    monkeypatch.setattr(qa_service, "super_agent", agent)
    monkeypatch.setattr(qa_service, "pg_manager", _fake_pg_manager())

    from noesis.schemas.login_vo import CurrentUser

    pending = MagicMock()
    pending.session_id = "session-1"
    pending.user_id = "user-1"
    pending.assistant_message_id = "msg-1"
    pending.expires_at = 0.0
    pending.action_requests = []
    pending.review_configs = []

    events = [
        event
        async for event in qa_service.QaService.exec_hitl_resume(
            pending=pending,
            decisions=[],
            grant_scope=None,
            current_user=CurrentUser(user_id="user-1", username="t"),
            db=MagicMock(),
            run_id="run-1",
        )
    ]

    assert isinstance(events, list)
    assert fake_resume_agent.kwargs.get("model_id") is None


def test_compaction_summarizer_follows_run_model() -> None:
    """摘要器未单独配置时沿用本 run 模型，不再静默落平台默认。"""
    from noesis.factory import _compaction_deps

    with (
        patch("noesis.factory.ModelConfig") as cfg,
        patch("noesis.factory.get_llm") as get_llm,
        patch("noesis.factory.resolve_context_max_tokens", return_value=128_000),
    ):
        cfg.summarization_enabled = True
        cfg.summarization_trigger_tokens = 96_000
        cfg.max_tokens = 4_096
        cfg.context_max_input_tokens = 128_000
        get_llm.return_value = object()

        deps = _compaction_deps(model=object(), model_id="token/glm-5.3-flash")

    assert deps, "summarization_enabled 时必须产出 compaction deps"
    get_llm.assert_called_once_with(
        purpose="summarization", model_id="token/glm-5.3-flash"
    )
