"""平台杂项接口用例（integration）：健康检查与模型目录。

此前两端点零覆盖（/api/models 仅被路由注册断言触碰过）。

前置与运行：

    cd backend && uv run app.py
    uv run pytest tests/api/test_platform_misc_api.py -m integration
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


def test_health_endpoint(auth_client) -> None:
    """GET /health：就绪状态下 200（无需登录）。"""
    import httpx

    resp = httpx.get("http://127.0.0.1:8089/health", timeout=10.0)
    assert resp.status_code == 200


def test_model_catalog_shape(auth_client) -> None:
    """GET /api/models：内置目录 + 用户自定义合并的平台模型目录。"""
    resp = auth_client.get("/api/models")
    resp.raise_for_status()
    data = resp.json()["data"]
    assert "models" in data and "default_id" in data
    assert isinstance(data["models"], list) and data["models"]
    assert all("id" in m for m in data["models"])
