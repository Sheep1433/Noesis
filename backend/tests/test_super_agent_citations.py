from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import noesis.agents.super_agent as super_agent


def _capture_agent_options(monkeypatch) -> dict:
    captured = {}
    monkeypatch.setattr(super_agent, "create_agent_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(super_agent, "ensure_user_memory_files", lambda _user_id: None)
    monkeypatch.setattr(super_agent, "build_web_search_tools", lambda backend=None: [])
    monkeypatch.setattr(super_agent, "resolve_skill_sources_for_session", lambda *_args: [])
    monkeypatch.setattr(super_agent, "_compile_task_worker", lambda *_args, **_kwargs: MagicMock())
    monkeypatch.setattr(super_agent, "HitlConfig", SimpleNamespace(enabled=False, ask_timeout_seconds=86_400))
    monkeypatch.setattr(super_agent.ContextResolver, "resolve", lambda *_args: SimpleNamespace(
        system_prompt="system",
        memory_sources=[],
    ))
    monkeypatch.setattr(super_agent.SuperAgent, "checkpointer", property(lambda _self: object()))
    monkeypatch.setattr(
        super_agent,
        "create_noesis_agent",
        lambda **kwargs: captured.update(kwargs) or "compiled",
    )
    return captured


@pytest.mark.asyncio
async def test_super_agent_keeps_normal_streaming_response(monkeypatch) -> None:
    captured = _capture_agent_options(monkeypatch)

    compiled = await super_agent.SuperAgent()._create_compiled_agent(
        user_id="1",
        session_id="session-1",
        model_id="mimo",
        mcp_tools=None,
        enabled_skills=None,
        file_list=None,
        db=None,
    )

    assert compiled == "compiled"
    assert "response_format" not in captured
