"""通道设置操作：读取 Delivery 状态、连接测试与受控测试投递。"""

from __future__ import annotations

import asyncio
import time
import uuid

from noesis.config.env import MessagingConfig
from noesis_server.domain.chat.delivery.channel_health import channel_health
from noesis_server.domain.chat.delivery.telegram.client import TelegramBotClient
from noesis_server.domain.chat.delivery.feishu.client import FeishuBotClient
from noesis_server.exceptions.exception import ConflictException
from noesis_server.services.messaging_channel_service import (
    MessagingChannelService,
    RuntimeChannelConfig,
)

_TEST_MESSAGE = "Noesis 通道测试成功。你可以返回设置页继续配置。"
_RATE_LIMIT_SECONDS = 10.0
_last_command: dict[tuple[str, str, str], float] = {}


class ChannelOperationsService:
    @staticmethod
    def _check_rate(user_id: int | str, channel_id: str, command: str) -> None:
        key = (str(user_id), channel_id, command)
        now = time.monotonic()
        if now - _last_command.get(key, 0) < _RATE_LIMIT_SECONDS:
            raise ConflictException(data={"code": "rate_limited"}, message="操作过于频繁，请稍后再试")
        _last_command[key] = now

    @staticmethod
    def _channel_client(
        cfg: RuntimeChannelConfig,
    ) -> FeishuBotClient | TelegramBotClient:
        if cfg.channel_type == "feishu":
            if not MessagingConfig.feishu_app_id or not MessagingConfig.feishu_app_secret:
                raise ConflictException(
                    data={"code": "service_unavailable"},
                    message="飞书服务暂不可用",
                )
            return FeishuBotClient(
                MessagingConfig.feishu_app_id,
                MessagingConfig.feishu_app_secret,
                timeout=10,
            )
        if not cfg.bot_token:
            raise ConflictException(
                data={"code": "credential_missing"},
                message="请先配置 Bot Token",
            )
        return TelegramBotClient(cfg.bot_token, timeout=10)

    @classmethod
    async def test_connection(cls, user_id: int | str, channel_id: str) -> dict:
        cls._check_rate(user_id, channel_id, "connection")
        cfg = MessagingChannelService.get_runtime_channel(user_id, channel_id)
        correlation_id = str(uuid.uuid4())
        if not cfg.enabled:
            raise ConflictException(data={"code": "channel_disabled"}, message="请先启用通道")
        client = cls._channel_client(cfg)
        probe = client.get_bot_info if cfg.channel_type == "feishu" else client.get_me
        try:
            await asyncio.wait_for(probe(), timeout=12)
            channel_health.report_status(user_id, channel_id, "healthy", "连接正常", correlation_id=correlation_id)
            return {"ok": True, "status": "healthy", "message": "连接正常", "checked_at": int(time.time() * 1000), "correlation_id": correlation_id}
        except TimeoutError:
            channel_health.report_status(user_id, channel_id, "unavailable", "连接超时，请稍后重试", error_category="timeout", correlation_id=correlation_id)
            return {"ok": False, "status": "unavailable", "message": "连接超时，请稍后重试", "checked_at": int(time.time() * 1000), "error_category": "timeout", "correlation_id": correlation_id}
        except Exception:
            channel_health.report_status(user_id, channel_id, "unavailable", "连接失败，请检查通道凭据和权限", error_category="authentication", correlation_id=correlation_id)
            return {"ok": False, "status": "unavailable", "message": "连接失败，请稍后重试", "checked_at": int(time.time() * 1000), "error_category": "authentication", "correlation_id": correlation_id}
        finally:
            await client.aclose()

    @classmethod
    async def test_delivery(cls, user_id: int | str, channel_id: str) -> dict:
        cls._check_rate(user_id, channel_id, "delivery")
        cfg = MessagingChannelService.get_runtime_channel(user_id, channel_id)
        correlation_id = str(uuid.uuid4())
        if not cfg.enabled:
            raise ConflictException(data={"code": "channel_disabled"}, message="请先启用通道")
        if not cfg.pairing_chat_id:
            raise ConflictException(data={"code": "channel_unpaired"}, message="请先完成通道配对")
        client = cls._channel_client(cfg)
        try:
            if cfg.channel_type == "feishu":
                result = await asyncio.wait_for(client.send_text(cfg.pairing_chat_id, _TEST_MESSAGE), timeout=12)
            else:
                result = await asyncio.wait_for(client.send_message(cfg.pairing_chat_id, _TEST_MESSAGE), timeout=12)
            channel_health.report_activity(user_id, channel_id, "outbound", "succeeded")
            channel_health.report_status(user_id, channel_id, "healthy", "测试消息已发送", correlation_id=correlation_id)
            message = result.get("message")
            external_id = result.get("message_id") or (
                message.get("message_id") if isinstance(message, dict) else None
            )
            return {"ok": True, "status": "delivered", "message": "测试消息已发送", "delivered_at": int(time.time() * 1000), "external_message_id": str(external_id or "") or None, "correlation_id": correlation_id}
        except TimeoutError:
            channel_health.report_activity(user_id, channel_id, "outbound", "failed")
            return {"ok": False, "status": "failed", "message": "发送超时，请稍后重试", "error_category": "timeout", "correlation_id": correlation_id}
        except Exception:
            channel_health.report_activity(user_id, channel_id, "outbound", "failed")
            return {"ok": False, "status": "failed", "message": "测试消息发送失败，请检查通道配置", "error_category": "delivery", "correlation_id": correlation_id}
        finally:
            await client.aclose()
