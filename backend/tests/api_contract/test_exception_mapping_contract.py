"""domain exception → HTTP 状态码 + envelope 的全局映射契约。

异常处理器与 ResponseUtil 包装只存在于 HTTP 层；service 单测无法覆盖
「抛出的异常最终长什么样」。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from noesis.errors.exceptions import (
    ConflictException,
    LoginException,
    NotFoundException,
    ServiceException,
)
from noesis.services.chat_service import ChatService
from noesis.services.login_service import LoginService
from noesis.services.run_service import RunService
from server.main import app


def test_login_exception_maps_to_400_failure_envelope(anon_client) -> None:
    with patch.object(
        LoginService,
        "authenticate_user",
        AsyncMock(side_effect=LoginException(data="", message="用户名或密码错误")),
    ):
        resp = anon_client.post(
            "/api/auth/login", data={"username": "u", "password": "p"}
        )
    assert resp.status_code == 400
    body = resp.json()
    # failure 的业务 code 是 WARN=601（HTTP 400）：钉住现状，前端按 json.code 分支
    assert body["code"] == 601
    assert body["success"] is False
    assert body["msg"] == "用户名或密码错误"


def test_not_found_exception_maps_to_404_envelope(contract_client) -> None:
    with patch.object(
        RunService,
        "get",
        AsyncMock(side_effect=NotFoundException(message="任务不存在")),
    ):
        resp = contract_client.get("/api/chat/runs/run-missing")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404
    assert body["success"] is False
    assert body["msg"] == "任务不存在"


def test_conflict_exception_maps_to_409_with_data(contract_client) -> None:
    # POST /runs 撞上活跃 run：409 + data.run_id 供前端「加入已有 run」
    dispatch = MagicMock()
    dispatch.handled = False
    dispatch.rewrite_request = None
    with (
        patch("server.api.chat_api.pg_manager") as chat_pg,
        patch("noesis.chat.commands.registry.dispatch", AsyncMock(return_value=dispatch)),
        patch.object(
            RunService,
            "create",
            AsyncMock(
                side_effect=ConflictException(
                    message="当前会话仍在生成",
                    data={"run_id": "run-active", "assistant_message_id": "msg-1"},
                )
            ),
        ),
    ):
        chat_pg.advisory_lock_ready = True
        resp = contract_client.post(
            "/api/chat/runs",
            json={
                "session_id": "sess-1",
                "content": "hello",
                "client_request_id": "cr-1-contract-0001",
            },
        )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == 409
    assert body["success"] is False
    assert body["msg"] == "当前会话仍在生成"
    assert body["data"]["run_id"] == "run-active"


def test_service_exception_maps_to_500_envelope(contract_client) -> None:
    with patch.object(
        ChatService,
        "get_session_by_id",
        AsyncMock(side_effect=ServiceException(message="DB 不可用")),
    ):
        resp = contract_client.get("/api/chat/sessions/sess-1")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == 500
    assert body["success"] is False


def test_unexpected_exception_maps_to_500_generic(contract_client) -> None:
    # starlette 对 Exception handler 会先回 500 响应再向上 re-raise；
    # TestClient 默认把 re-raise 透传进测试，须关掉才能读到响应
    from fastapi.testclient import TestClient

    with patch.object(
        ChatService,
        "get_session_by_id",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("noesis_session", "raw-session")
        client.headers["X-CSRF-Token"] = "contract-csrf-current"
        resp = client.get("/api/chat/sessions/sess-1")
    assert resp.status_code == 500
    assert resp.json()["code"] == 500
