"""Telegram 子包。"""

# 避免与 channels.build_default_registry 循环导入；按需从子模块 import。
__all__ = [
    "TelegramBotClient",
    "TelegramChannelAdapter",
    "mask_bot_token",
    "extract_plain_text_from_parts",
]


def __getattr__(name: str):
    if name in ("TelegramBotClient", "mask_bot_token"):
        from domain.chat.delivery.telegram.client import TelegramBotClient, mask_bot_token

        return {"TelegramBotClient": TelegramBotClient, "mask_bot_token": mask_bot_token}[name]
    if name in ("TelegramChannelAdapter", "extract_plain_text_from_parts"):
        from domain.chat.delivery.telegram.adapter import (
            TelegramChannelAdapter,
            extract_plain_text_from_parts,
        )

        return {
            "TelegramChannelAdapter": TelegramChannelAdapter,
            "extract_plain_text_from_parts": extract_plain_text_from_parts,
        }[name]
    if name == "TelegramOutbound":
        from domain.chat.delivery.telegram.stream_out import TelegramOutbound

        return TelegramOutbound
    raise AttributeError(name)
