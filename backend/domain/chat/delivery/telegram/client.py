"""Telegram Bot API 异步客户端（httpx）。

代理策略对齐常见 IM 实现（如 clowder-ai/grammy）：不设专用 proxy 配置项，
由进程环境 ``HTTP(S)_PROXY`` / ``ALL_PROXY`` 或系统透明代理负责出网；
``trust_env=True``。LLM 客户端仍保持 ``trust_env=False``，互不影响。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from common.logging import logger


def mask_bot_token(token: str) -> str:
    t = str(token or "")
    if len(t) <= 8:
        return "****"
    return f"****{t[-4:]}"


class TelegramBotClient:
    def __init__(
        self,
        bot_token: str,
        *,
        timeout: float = 60.0,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self.bot_token = bot_token
        self._masked = mask_bot_token(bot_token)
        self._base = f"{base_url.rstrip('/')}/bot{bot_token}"
        read_sec = max(float(timeout), 30.0)
        http_timeout = httpx.Timeout(
            connect=15.0,
            read=read_sec,
            write=15.0,
            pool=15.0,
        )
        self._client = httpx.AsyncClient(
            timeout=http_timeout,
            trust_env=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self._base}/{method}"
        try:
            resp = await self._client.post(url, json=payload or {})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            # 勿 logger.exception：traceback locals 会带上含 token 的完整 URL
            logger.warning(
                "Telegram API {} failed bot={} err={}: {}",
                method,
                self._masked,
                type(exc).__name__,
                str(exc) or "(no message)",
            )
            raise RuntimeError(
                f"telegram {method} network error: {type(exc).__name__}"
            ) from None
        if not data.get("ok"):
            desc = data.get("description") or "unknown"
            logger.warning(
                "Telegram API {} not ok bot={} desc={}", method, self._masked, desc
            )
            raise RuntimeError(f"telegram {method}: {desc}")
        return data.get("result")

    async def get_updates(
        self,
        *,
        offset: Optional[int] = None,
        timeout: int = 25,
        allowed_updates: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"timeout": int(timeout)}
        if offset is not None:
            payload["offset"] = int(offset)
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        result = await self._call("getUpdates", payload)
        return list(result or [])

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        body = (text or "")[:4096]
        if not body.strip():
            body = "(空回复)"
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": body}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("sendMessage", payload)

    async def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        body = (text or "")[:4096]
        if not body.strip():
            body = "(空回复)"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": body,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("editMessageText", payload)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> Any:
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        if show_alert:
            payload["show_alert"] = True
        return await self._call("answerCallbackQuery", payload)

    async def edit_message_reply_markup(
        self,
        chat_id: str | int,
        message_id: int,
        *,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "reply_markup": reply_markup or {"inline_keyboard": []},
        }
        return await self._call("editMessageReplyMarkup", payload)
