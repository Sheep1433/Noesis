"""飞书 OpenAPI 异步客户端；WebSocket 入站由官方 SDK 维护。"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx


def mask_app_id(app_id: str) -> str:
    value = str(app_id or "")
    return f"{value[:4]}…{value[-4:]}" if len(value) > 10 else "****"


class FeishuBotClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base_url: str = "https://open.feishu.cn",
        timeout: float = 20.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http_client is None
        self._token = ""
        self._token_expires_at = 0.0

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _tenant_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        response = await self._http.post(
            f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self._app_secret},
        )
        response.raise_for_status()
        data = response.json()
        if int(data.get("code") or 0) != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(f"feishu authentication failed: code={data.get('code')}")
        self._token = str(data["tenant_access_token"])
        self._token_expires_at = time.monotonic() + max(30, int(data.get("expire") or 7200) - 120)
        return self._token

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._tenant_token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        response = await self._http.request(method, f"{self._base_url}{path}", headers=headers, **kwargs)
        response.raise_for_status()
        data = response.json()
        if int(data.get("code") or 0) != 0:
            raise RuntimeError(f"feishu api failed: code={data.get('code')}")
        return data

    async def get_bot_info(self) -> dict[str, Any]:
        return await self._request("GET", "/open-apis/bot/v3/info")

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/open-apis/im/v1/messages?receive_id_type=chat_id",
            json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        )
        return dict(data.get("data") or {})

    async def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        data = await self._request(
            "POST",
            f"/open-apis/im/v1/messages/{message_id}/reply",
            json={"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        )
        return dict(data.get("data") or {})

    async def update_text(self, message_id: str, text: str) -> dict[str, Any]:
        data = await self._request(
            "PUT",
            f"/open-apis/im/v1/messages/{message_id}",
            json={"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        )
        return dict(data.get("data") or {})

    async def send_card(self, chat_id: str, card: dict[str, Any]) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/open-apis/im/v1/messages?receive_id_type=chat_id",
            json={"receive_id": chat_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
        )
        return dict(data.get("data") or {})
