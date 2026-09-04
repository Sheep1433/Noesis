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


# ---------- 默认模型偏好的消费（2026-09-03 问题 7） ----------


class _Snapshot:
    def __init__(self, model_id: str) -> None:
        self.id = model_id
        self.purpose = "chat"


@pytest.mark.asyncio
async def test_resolution_falls_back_to_user_preference_before_platform_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求无显式 model_id、会话无 extra 时：用户偏好先于平台目录默认。"""
    from types import SimpleNamespace

    from noesis.services.qa import helpers
    from noesis.services.user_llm_service import UserLLMService

    async def no_extra_session(session_id: str, user_id: str, db: object):
        return SimpleNamespace(extra=None)

    async def fake_snapshots(db, *, user_id: str, model_id: str | None):
        if model_id == "token/glm-5.3-flash":
            return [_Snapshot("token/glm-5.3-flash")]
        return []

    async def fake_preference(db, *, user_id: str):
        return "token/glm-5.3-flash"

    monkeypatch.setattr(helpers.ChatService, "get_session_by_id", staticmethod(no_extra_session))
    monkeypatch.setattr(UserLLMService, "resolve_runtime_snapshots", staticmethod(fake_snapshots))
    monkeypatch.setattr(UserLLMService, "get_default_model", staticmethod(fake_preference))
    monkeypatch.setattr(helpers, "get_default_model_id", lambda: "kilo-auto/free")

    resolved = await helpers._resolve_model_for_query(
        session_id="session-1",
        user_id="user-1",
        request_model_id=None,
        db=object(),
    )
    assert resolved == "token/glm-5.3-flash"


@pytest.mark.asyncio
async def test_preference_pointing_to_unresolvable_model_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """偏好指向已删除的自定义模型：报错，不静默回退平台默认（2026-09-03 裁决）。"""
    from types import SimpleNamespace

    import pytest as _pytest

    from noesis.errors.exceptions import NotFoundException
    from noesis.services.qa import helpers
    from noesis.services.user_llm_service import UserLLMService

    async def no_extra_session(session_id: str, user_id: str, db: object):
        return SimpleNamespace(extra=None)

    async def no_snapshots(db, *, user_id: str, model_id: str | None):
        return []

    async def stale_preference(db, *, user_id: str):
        return "deleted/custom-model"

    monkeypatch.setattr(helpers.ChatService, "get_session_by_id", staticmethod(no_extra_session))
    monkeypatch.setattr(UserLLMService, "resolve_runtime_snapshots", staticmethod(no_snapshots))
    monkeypatch.setattr(UserLLMService, "get_default_model", staticmethod(stale_preference))
    monkeypatch.setattr(helpers, "get_default_model_id", lambda: "kilo-auto/free")

    with _pytest.raises(NotFoundException) as exc_info:
        await helpers._resolve_model_for_query(
            session_id="session-1",
            user_id="user-1",
            request_model_id=None,
            db=object(),
        )
    assert "模型不存在或已失效" in (exc_info.value.message or "")


@pytest.mark.asyncio
async def test_request_explicit_unresolvable_model_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求显式携带不可解析的 model_id：报错，不静默换平台默认。"""

    import pytest as _pytest

    from noesis.errors.exceptions import NotFoundException
    from noesis.services.qa import helpers
    from noesis.services.user_llm_service import UserLLMService

    async def no_snapshots(db, *, user_id: str, model_id: str | None):
        return []

    async def merge_extra(session_id: str, user_id: str, patch: dict, db: object):
        return None

    monkeypatch.setattr(UserLLMService, "resolve_runtime_snapshots", staticmethod(no_snapshots))
    monkeypatch.setattr(helpers.ChatService, "merge_session_extra", staticmethod(merge_extra))

    with _pytest.raises(NotFoundException) as exc_info:
        await helpers._resolve_model_for_query(
            session_id="session-1",
            user_id="user-1",
            request_model_id="ghost/model-x",
            db=object(),
        )
    assert "模型不存在或已失效" in (exc_info.value.message or "")


@pytest.mark.asyncio
async def test_session_extra_unresolvable_model_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话 extra 里存的模型 id 不可解析（如 Provider 数据清理后）：报错。"""
    from types import SimpleNamespace

    import pytest as _pytest

    from noesis.errors.exceptions import NotFoundException
    from noesis.services.qa import helpers
    from noesis.services.user_llm_service import UserLLMService

    async def stale_session(session_id: str, user_id: str, db: object):
        return SimpleNamespace(extra={"model_id": "gone/model-y"})

    async def no_snapshots(db, *, user_id: str, model_id: str | None):
        return []

    monkeypatch.setattr(helpers.ChatService, "get_session_by_id", staticmethod(stale_session))
    monkeypatch.setattr(UserLLMService, "resolve_runtime_snapshots", staticmethod(no_snapshots))

    with _pytest.raises(NotFoundException) as exc_info:
        await helpers._resolve_model_for_query(
            session_id="session-1",
            user_id="user-1",
            request_model_id=None,
            db=object(),
        )
    assert "模型不存在或已失效" in (exc_info.value.message or "")


@pytest.mark.asyncio
async def test_request_explicit_builtin_catalog_model_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求显式携带内置目录 id：strict 解析命中内置条目，正常返回。"""

    from noesis.services.qa import helpers
    from noesis.services.user_llm_service import UserLLMService

    async def no_snapshots(db, *, user_id: str, model_id: str | None):
        return []

    async def merge_extra(session_id: str, user_id: str, patch: dict, db: object):
        merge_extra.patch = patch

    merge_extra.patch = None
    from noesis.llm.catalog import get_model_catalog

    builtin_id = get_model_catalog()[0].id
    monkeypatch.setattr(UserLLMService, "resolve_runtime_snapshots", staticmethod(no_snapshots))
    monkeypatch.setattr(helpers.ChatService, "merge_session_extra", staticmethod(merge_extra))

    resolved = await helpers._resolve_model_for_query(
        session_id="session-1",
        user_id="user-1",
        request_model_id=builtin_id,
        db=object(),
    )
    assert resolved == builtin_id
    assert merge_extra.patch == {"model_id": builtin_id}
