"""请求体校验失败的响应形状契约。

仓库未注册 RequestValidationError 处理器 → FastAPI 默认 422 {detail: [...]}，
与业务 envelope 形状不同；前端错误处理依赖这一事实，用测试钉住。
若未来统一 envelope，改这里即可发现所有受影响调用方。
"""

from __future__ import annotations


def test_create_session_wrong_type_returns_fastapi_422(contract_client) -> None:
    resp = contract_client.post(
        "/api/chat/sessions", json={"title": 123, "extra": "not-a-dict"}
    )
    assert resp.status_code == 422
    body = resp.json()
    # FastAPI 默认形状：detail 数组（非业务 envelope）
    assert isinstance(body.get("detail"), list)
    assert "success" not in body


def test_create_run_missing_required_fields_returns_422(contract_client) -> None:
    resp = contract_client.post("/api/chat/runs", json={"session_id": "sess-1"})
    assert resp.status_code == 422
    missing = {
        item["loc"][-1] for item in resp.json()["detail"] if item["type"] == "missing"
    }
    assert {"content", "client_request_id"} & missing
