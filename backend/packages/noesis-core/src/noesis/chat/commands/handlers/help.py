"""/help —— 列出所有可用斜杠命令。"""
from __future__ import annotations

from noesis.chat.commands.registry import command, list_command_descriptions
from noesis.chat.commands.result import CommandResult
from noesis.chat.delivery.channels import InboundMessage


@command("help", description="列出所有可用斜杠命令")
async def help_cmd(ev: InboundMessage) -> CommandResult:
    lines = [f"/{name} — {desc or '(无描述)'}" for name, desc in list_command_descriptions()]
    return CommandResult(handled=True, text="可用命令:\n" + "\n".join(lines))
