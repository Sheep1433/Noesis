"""设置控制面与通知渠道接口用例（integration）：capabilities/审计/通知偏好/诊断/导入导出/重置 + 渠道 CRUD。

此前两模块零覆盖。导入导出闭环用同一 manifest 走 preview→apply；
重置放最后一步（恢复默认，不影响其它用例）。渠道 test-connection /
test-delivery 的健康路径需要真实凭据，且启用假 token 渠道会触发
runtime 轮询循环，故只断言禁用态守卫契约（409 channel_disabled）。

前置与运行：

    cd backend && uv run app.py
    uv run pytest tests/api/test_settings_surface_api.py -m integration
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration]


def test_settings_capabilities_and_diagnostics(auth_client) -> None:
    resp = auth_client.get("/api/user/settings/capabilities")
    resp.raise_for_status()
    assert isinstance(resp.json()["data"], dict)

    resp = auth_client.get("/api/user/settings/diagnostics")
    resp.raise_for_status()
    assert isinstance(resp.json()["data"], dict)


def test_settings_audit_lists_entries(auth_client) -> None:
    resp = auth_client.get(
        "/api/user/settings/audit", params={"page": 1, "page_size": 20}
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    assert isinstance(data, dict)


def test_notification_preferences_roundtrip(auth_client) -> None:
    """通知偏好：列表枚举全组合；写一条后状态与版本号更新。"""
    resp = auth_client.get("/api/user/settings/notifications")
    resp.raise_for_status()
    items = resp.json()["data"]["items"]
    assert items, "通知偏好应枚举全部 event×surface 组合"

    target = next(
        item
        for item in items
        if item["event_type"] == "hitl.pending" and item["delivery_surface"] == "web"
    )
    resp = auth_client.put(
        "/api/user/settings/notifications",
        json={
            "event_type": "hitl.pending",
            "delivery_surface": "web",
            "enabled": not target["enabled"],
        },
    )
    resp.raise_for_status()
    updated = resp.json()["data"]
    assert updated["enabled"] == (not target["enabled"])
    assert updated["version"] == target["version"] + 1

    # 恢复默认值，避免影响其它用例
    auth_client.put(
        "/api/user/settings/notifications",
        json={
            "event_type": "hitl.pending",
            "delivery_surface": "web",
            "enabled": target["enabled"],
        },
    )


def test_export_preview_apply_and_reset(auth_client) -> None:
    """导出→导入预览（得 preview_id）→导入应用→重置恢复默认。"""
    resp = auth_client.get("/api/user/settings/export")
    resp.raise_for_status()
    manifest = resp.json()["data"]
    assert isinstance(manifest, dict) and manifest

    resp = auth_client.post(
        "/api/user/settings/import/preview", json={"manifest": manifest}
    )
    resp.raise_for_status()
    preview = resp.json()["data"]
    preview_id = preview.get("preview_id")
    assert preview_id, "导入预览应返回 preview_id"

    resp = auth_client.post(
        "/api/user/settings/import/apply",
        json={"manifest": manifest, "preview_id": preview_id},
    )
    resp.raise_for_status()

    resp = auth_client.post("/api/user/settings/reset")
    resp.raise_for_status()


def test_channel_crud_and_contract_tests(auth_client) -> None:
    """渠道创建→列表→更新→连通/投递测试（端点契约）→删除。"""
    display = f"接口验证渠道-{uuid.uuid4().hex[:6]}"
    try:
        resp = auth_client.post(
            "/api/user/channels",
            json={
                "type": "telegram",
                "enabled": False,
                "display_name": display,
                "bot_token": "000000:FAKE_TOKEN_FOR_API_TEST",
                "bot_token_action": "replace",
                "pairing_chat_id": "000000000",
            },
        )
        resp.raise_for_status()
        channel = resp.json()["data"]
        channel_id = channel["channel_id"]
        assert channel["display_name"] == display

        resp = auth_client.get("/api/user/channels")
        resp.raise_for_status()
        assert any(c["channel_id"] == channel_id for c in resp.json()["data"]["channels"])

        resp = auth_client.put(
            f"/api/user/channels/{channel_id}",
            json={
                "type": "telegram",
                "enabled": False,
                "display_name": display + "-改名",
                "pairing_chat_id": "000000000",
            },
        )
        resp.raise_for_status()
        assert resp.json()["data"]["display_name"] == display + "-改名"

        # 连通/投递测试的健康路径需要真实凭据；且启用假 token 渠道会触发
        # runtime 轮询循环（渠道删除后并不停止），故只断言禁用态守卫契约：
        # 409 channel_disabled，不发起任何外网请求。
        resp = auth_client.post(f"/api/user/channels/{channel_id}/test-connection")
        assert resp.status_code == 409
        assert "channel_disabled" in resp.text

        resp = auth_client.post(f"/api/user/channels/{channel_id}/test-delivery")
        assert resp.status_code == 409
        assert "channel_disabled" in resp.text

        resp = auth_client.delete(f"/api/user/channels/{channel_id}")
        resp.raise_for_status()
        resp = auth_client.get("/api/user/channels")
        resp.raise_for_status()
        assert all(c["channel_id"] != channel_id for c in resp.json()["data"]["channels"])
    finally:
        # 兜底：按 display 前缀清除本用例创建的渠道（幂等）
        resp = auth_client.get("/api/user/channels")
        if resp.status_code == 200:
            for c in resp.json()["data"]["channels"]:
                if str(c.get("display_name", "")).startswith("接口验证渠道-"):
                    auth_client.delete(f"/api/user/channels/{c['channel_id']}")
