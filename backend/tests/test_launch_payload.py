"""Launch payload 契约：schema 化、白名单透传、敏感键拒绝、模型身份冻结。"""

from __future__ import annotations

import pytest

from noesis.chat.runs.launch_payload import (
    LAUNCH_PAYLOAD_VERSION,
    LaunchPayload,
    LaunchPayloadError,
)
from noesis.schemas.chat_vo import CreateRunRequest


def _request(extra: dict | None = None) -> CreateRunRequest:
    return CreateRunRequest(
        session_id="session-1",
        content="帮我查下故障",
        client_request_id="client-req-1",
        extra=extra if extra is not None else {"qa_type": "COMMON_QA"},
    )


def test_from_request_sanitizes_extra_whitelist() -> None:
    request = _request(
        {
            "qa_type": "SUPER_AGENT_QA",
            "file_dict": {"a.pdf": "att-1"},
            "kb_collections": ["kb-1"],
            "kb_search_enabled": True,
            "mcp_servers": ["mcp-x"],
            "enabled_skills": ["skill-y"],
            "mentions": ["@kb"],
            "model_id": "custom-model",
            # 白名单外的键不得进入 payload
            "session_secret": "x",
            "internal_flag": True,
        }
    )
    payload = LaunchPayload.from_create_request(
        request,
        user_id="user-1",
        assistant_message_id="msg-1",
        qa_type="SUPER_AGENT_QA",
        origin="web",
        resolved_model="custom-model",
    )
    data = payload.to_dict()
    assert data["extra"]["file_dict"] == {"a.pdf": "att-1"}
    assert data["extra"]["kb_collections"] == ["kb-1"]
    assert data["extra"]["mcp_servers"] == ["mcp-x"]
    assert "session_secret" not in data["extra"]
    assert "internal_flag" not in data["extra"]
    assert payload.resolved_model == "custom-model"
    assert payload.schema_version == LAUNCH_PAYLOAD_VERSION


def test_sensitive_keys_in_request_are_dropped() -> None:
    """请求 extra 混入敏感键：静默丢弃（白名单过滤），payload 不含秘密。"""
    request = _request({"qa_type": "COMMON_QA", "api_key": "sk-xxx", "csrf_token": "t"})
    payload = LaunchPayload.from_create_request(
        request,
        user_id="user-1",
        assistant_message_id="msg-1",
        qa_type="COMMON_QA",
        origin="web",
        resolved_model=None,
    )
    serialized = str(payload.to_dict())
    assert "sk-xxx" not in serialized and "csrf_token" not in serialized


def test_roundtrip_rebuilds_qa_request_with_frozen_model() -> None:
    request = _request({"qa_type": "COMMON_QA", "model_id": None})
    payload = LaunchPayload.from_create_request(
        request,
        user_id="user-1",
        assistant_message_id="msg-1",
        qa_type="COMMON_QA",
        origin="web",
        resolved_model="model-frozen",
    )
    rebuilt = LaunchPayload.from_dict(payload.to_dict())
    qa_request = rebuilt.to_qa_query_request()
    assert qa_request.query == "帮我查下故障"
    assert qa_request.qa_type == "COMMON_QA"
    assert qa_request.chat_id == "session-1"
    # 模型身份冻结：排队期间会话默认变化不影响本 run
    assert qa_request.model_id == "model-frozen"


def test_unknown_schema_version_rejected() -> None:
    with pytest.raises(LaunchPayloadError, match="schema_version"):
        LaunchPayload.from_dict({"schema_version": 99})


def test_dispatcher_context_has_no_request_objects() -> None:
    """payload 不得携带 CreateRunRequest/CurrentUser——dispatcher 只见 DB 行。"""
    payload = LaunchPayload.from_create_request(
        _request(),
        user_id="user-1",
        assistant_message_id="msg-1",
        qa_type="COMMON_QA",
        origin="web",
        resolved_model="m",
    )
    data = payload.to_dict()
    assert isinstance(data, dict)
    assert "current_user" not in data and "request" not in data


# ============ 推理档位（reasoning_effort）白名单 ============


def _reasoning_payload(extra: dict):
    """构造带 reasoning_effort 的 create 请求载荷（复用现有测试的请求形状）。"""
    from noesis.chat.runs.launch_payload import LaunchPayload
    from noesis.schemas.chat_vo import CreateRunRequest

    request = CreateRunRequest(
        session_id="sess-1",
        content="hello",
        client_request_id="cr-reasoning-0001",
        extra=extra,
    )
    return LaunchPayload.from_create_request(
        request,
        user_id="user-1",
        assistant_message_id="msg-1",
        qa_type="COMMON_QA",
        origin="web",
        resolved_model="model-x",
    )


def test_reasoning_effort_passes_whitelist_and_freezes() -> None:
    payload = _reasoning_payload({"reasoning_effort": "high", "unknown_key": 1})
    assert payload.extra["reasoning_effort"] == "high"
    assert "unknown_key" not in payload.extra
    qa_request = payload.to_qa_query_request()
    assert qa_request.reasoning_effort == "high"


def test_reasoning_effort_invalid_value_silently_dropped() -> None:
    for bad in ("xhigh", "auto", 123, None):
        payload = _reasoning_payload({"reasoning_effort": bad})
        assert "reasoning_effort" not in payload.extra
        assert payload.to_qa_query_request().reasoning_effort is None
