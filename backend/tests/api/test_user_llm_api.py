"""自定义模型服务接口用例（integration）：Provider / 模型 CRUD、偏好、草案发现。

此前整模块零覆盖。discover 用本地起的 OpenAI 兼容假服务（``GET /models``），
不依赖外网；默认模型偏好会先取旧值、断言后恢复，避免影响其它用例的模型解析。

清理约定：所有用例以唯一 slug 创建 Provider，``finally`` 按 slug 兜底删除——
即使中间任何断言失败，也不会在用户数据里留下测试 Provider/模型条目。

前置与运行：

    cd backend && uv run app.py
    uv run pytest tests/api/test_user_llm_api.py -m 'integration and not llm'
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = [pytest.mark.integration]


class _FakeOpenAIModelsHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容 GET /models：返回固定模型列表，供 discover 探测。"""

    def do_GET(self):  # noqa: N802 (http.server 约定)
        body = json.dumps(
            {"data": [{"id": "fake-model-a"}, {"id": "fake-model-b"}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静默访问日志
        pass


@pytest.fixture(scope="module")
def fake_openai_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAIModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _unique_slug() -> str:
    return f"api-llm-{uuid.uuid4().hex[:8]}"


def _cleanup_provider(auth_client, slug: str) -> None:
    """按 slug 兜底删除该测试创建的 Provider 及其下模型（幂等）。"""
    resp = auth_client.get("/api/user/llm/providers")
    if resp.status_code != 200:
        return
    for p in resp.json()["data"]["providers"]:
        if p.get("slug") == slug:
            auth_client.delete(f"/api/user/llm/providers/{p['provider_id']}")


def _create_provider(auth_client, slug: str, base_url: str, name: str) -> dict:
    resp = auth_client.post(
        "/api/user/llm/providers",
        json={
            "name": name,
            "slug": slug,
            "api_type": "openai",
            "base_url": base_url,
            "enabled": True,
            "api_key": "test-key",
            "api_key_action": "replace",
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]


def test_provider_crud_roundtrip(auth_client, fake_openai_server) -> None:
    """创建→列表可见→更名→删除→列表消失。"""
    slug = _unique_slug()
    try:
        provider = _create_provider(auth_client, slug, fake_openai_server, "接口验证 Provider")
        provider_id = provider["provider_id"]
        assert provider["slug"] == slug

        resp = auth_client.get("/api/user/llm/providers")
        resp.raise_for_status()
        assert any(p["provider_id"] == provider_id for p in resp.json()["data"]["providers"])

        resp = auth_client.put(
            f"/api/user/llm/providers/{provider_id}",
            json={"name": "接口验证 Provider 改名", "api_key_action": "keep"},
        )
        resp.raise_for_status()
        assert resp.json()["data"]["name"] == "接口验证 Provider 改名"

        resp = auth_client.delete(f"/api/user/llm/providers/{provider_id}")
        resp.raise_for_status()
        resp = auth_client.get("/api/user/llm/providers")
        resp.raise_for_status()
        assert all(p["provider_id"] != provider_id for p in resp.json()["data"]["providers"])
    finally:
        _cleanup_provider(auth_client, slug)


def test_model_crud_roundtrip(auth_client, fake_openai_server) -> None:
    """模型条目挂在 Provider 下：创建→列表→更新→删除，条目身份 {slug}/{model_id}。"""
    slug = _unique_slug()
    try:
        provider_id = _create_provider(
            auth_client, slug, fake_openai_server, "模型条目宿主"
        )["provider_id"]

        resp = auth_client.post(
            "/api/user/llm/models",
            json={
                "provider_id": provider_id,
                "model_id": "fake-model-a",
                "label": "接口验证模型",
                "context_window": 8192,
            },
        )
        resp.raise_for_status()
        entry = resp.json()["data"]
        entry_id = entry["entry_id"]
        assert entry["model_id"] == "fake-model-a"

        resp = auth_client.get("/api/user/llm/models")
        resp.raise_for_status()
        assert any(
            m["entry_id"] == entry_id for m in resp.json()["data"]["models"]
        )

        resp = auth_client.put(
            f"/api/user/llm/models/{entry_id}",
            json={
                "provider_id": provider_id,
                "model_id": "fake-model-a",
                "label": "接口验证模型改名",
            },
        )
        resp.raise_for_status()
        assert resp.json()["data"]["label"] == "接口验证模型改名"

        resp = auth_client.delete(f"/api/user/llm/models/{entry_id}")
        resp.raise_for_status()
    finally:
        _cleanup_provider(auth_client, slug)


def test_default_model_preference_roundtrip(auth_client, fake_openai_server) -> None:
    """默认模型偏好：读旧值→设置→生效→恢复旧值。"""
    slug = _unique_slug()
    original = None
    try:
        resp = auth_client.get("/api/user/llm/preferences")
        resp.raise_for_status()
        original = resp.json().get("data", {}).get("default_model_id")

        provider_id = _create_provider(
            auth_client, slug, fake_openai_server, "偏好宿主"
        )["provider_id"]
        resp = auth_client.post(
            "/api/user/llm/models",
            json={"provider_id": provider_id, "model_id": "fake-model-a"},
        )
        resp.raise_for_status()
        model_identity = f"{slug}/fake-model-a"

        resp = auth_client.put(
            "/api/user/llm/preferences",
            json={"default_model_id": model_identity},
        )
        resp.raise_for_status()
        assert resp.json()["data"]["default_model_id"] == model_identity

        resp = auth_client.get("/api/user/llm/preferences")
        resp.raise_for_status()
        assert resp.json()["data"]["default_model_id"] == model_identity
    finally:
        auth_client.put(
            "/api/user/llm/preferences",
            json={"default_model_id": original},
        )
        _cleanup_provider(auth_client, slug)


def test_discover_draft_models_via_local_endpoint(
    auth_client, fake_openai_server
) -> None:
    """草案发现：OpenAI 兼容端点返回模型列表，不落库。"""
    resp = auth_client.post(
        "/api/user/llm/providers/discover",
        json={"api_type": "openai", "base_url": fake_openai_server, "api_key": "test-key"},
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    assert data["ok"] is True
    model_ids = {m.get("model_id") or m.get("id") for m in data["models"]}
    assert {"fake-model-a", "fake-model-b"} <= model_ids
