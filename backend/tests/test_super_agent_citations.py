from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.structured_output import ToolStrategy

import noesis.agents.super_agent as super_agent
from noesis.runtime.evidence import CitedAnswer


def _capture_agent_options(monkeypatch, *, citations_enabled: bool) -> dict:
    captured = {}
    monkeypatch.setattr(super_agent, "create_agent_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(super_agent, "ensure_user_memory_files", lambda _user_id: None)
    monkeypatch.setattr(super_agent, "build_web_search_tools", lambda: [])
    monkeypatch.setattr(super_agent, "resolve_skill_sources_for_session", lambda *_args: [])
    monkeypatch.setattr(super_agent, "_build_task_worker_subagents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(super_agent, "_build_memory_middleware", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(super_agent, "TodoListMiddleware", lambda: object())
    monkeypatch.setattr(super_agent, "RevisableSkillsMiddleware", lambda **_kwargs: object())
    monkeypatch.setattr(super_agent, "HitlConfig", SimpleNamespace(enabled=False))
    monkeypatch.setattr(super_agent.ContextResolver, "resolve", lambda *_args: SimpleNamespace(
        system_prompt="system",
        memory_sources=[],
    ))
    monkeypatch.setattr(super_agent.SuperAgent, "checkpointer", property(lambda _self: object()))
    monkeypatch.setattr(
        super_agent,
        "structured_citations_enabled",
        lambda _model_id: citations_enabled,
    )
    monkeypatch.setattr(
        super_agent,
        "create_noesis_agent",
        lambda **kwargs: captured.update(kwargs) or "compiled",
    )
    return captured


async def _compile_super_agent(model_id: str):
    return await super_agent.SuperAgent()._create_compiled_agent(
        user_id="1",
        session_id="session-1",
        model_id=model_id,
        mcp_tools=None,
        enabled_skills=None,
        file_list=None,
        db=None,
    )


@pytest.mark.asyncio
async def test_super_agent_uses_cited_answer_for_verified_model(monkeypatch) -> None:
    captured = _capture_agent_options(monkeypatch, citations_enabled=True)

    compiled = await _compile_super_agent("mimo")

    assert compiled == "compiled"
    assert isinstance(captured["response_format"], ToolStrategy)
    assert captured["response_format"].schema is CitedAnswer


@pytest.mark.asyncio
async def test_super_agent_keeps_plain_text_for_unverified_model(monkeypatch) -> None:
    captured = _capture_agent_options(monkeypatch, citations_enabled=False)

    await _compile_super_agent("flash")

    assert "response_format" not in captured
