"""/model —— 查看当前模型与可切换 catalog。"""
from __future__ import annotations

from noesis.chat.commands.result import CommandResult
from noesis.chat.commands.registry import command
from noesis.chat.delivery.channels import InboundMessage
from noesis.llm.catalog import get_default_model_id, get_model_catalog


@command("model", description="查看当前模型与可切换 catalog")
async def model_cmd(ev: InboundMessage) -> CommandResult:
    default_id = get_default_model_id()
    entries = get_model_catalog()
    lines = []
    for e in entries:
        mark = " (默认)" if e.id == default_id else ""
        lines.append(f"- {e.id} — {e.label}{mark}")
    return CommandResult(
        handled=True, text="当前默认模型: " + default_id + "\ncatalog:\n" + "\n".join(lines)
    )
