from __future__ import annotations

import pytest

from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.qa_vo import QaQueryRequest
from noesis.services.qa import service as qa_service


@pytest.mark.asyncio
async def test_exec_query_resolves_model_without_run_id_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    async def resolve_model(*, session_id: str, user_id: str, request_model_id: str | None, db: object) -> str:
        calls.update({
            "session_id": session_id,
            "user_id": user_id,
            "request_model_id": request_model_id,
            "db": db,
        })
        return "model-1"

    async def seed_stats(*args: object, **kwargs: object) -> None:
        return None

    async def no_mcp(**kwargs: object) -> list[str]:
        return []

    monkeypatch.setattr(qa_service, "_resolve_model_for_query", resolve_model)
    monkeypatch.setattr(qa_service, "seed_session_stats_from_history", seed_stats)
    monkeypatch.setattr(qa_service, "_resolve_mcp_servers_for_query", no_mcp)
    monkeypatch.setattr(qa_service, "_resolve_enabled_skills_for_query", no_mcp)

    db = object()
    events = [
        event
        async for event in qa_service.QaService.exec_query(
            QaQueryRequest(query="hello", qa_type="UNKNOWN", chat_id="session-1"),
            CurrentUser(user_id="user-1", username="tester"),
            db,
            run_id="run-1",
        )
    ]

    assert events
    assert calls == {
        "session_id": "session-1",
        "user_id": "user-1",
        "request_model_id": None,
        "db": db,
    }
