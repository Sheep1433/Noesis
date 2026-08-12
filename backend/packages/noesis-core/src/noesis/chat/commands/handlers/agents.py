"""/agents —— 列出可用 qa_type（权威来源：core 的 IntentEnum）。"""
from __future__ import annotations

from noesis.chat.commands.result import CommandResult
from noesis.chat.commands.registry import command
from noesis.config.code_enum import IntentEnum
from noesis.chat.delivery.channels import InboundMessage


@command("agents", description="列出可用的问答类型 qa_type")
async def agents_cmd(ev: InboundMessage) -> CommandResult:
    lines = [f"- {item.value[0]} — {item.value[1]}" for item in IntentEnum]
    return CommandResult(handled=True, text="可用 qa_type:\n" + "\n".join(lines))
