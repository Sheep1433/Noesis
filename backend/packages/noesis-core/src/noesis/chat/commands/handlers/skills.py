"""/skills —— 列出已安装 skill 包（命令名 + 描述）。

列表中的每个条目天然对应一个（D 类）可调用的 /命令名；二者走同一份 skill
扫描结果，避免「列表里有但调不通」。
"""
from __future__ import annotations

from noesis.chat.commands.result import CommandResult
from noesis.chat.commands.registry import command
from noesis.chat.config_skills_scan import scan_installed_skills
from noesis.chat.delivery.channels import InboundMessage


@command("skills", description="列出已安装的 skill 包")
async def skills_cmd(ev: InboundMessage) -> CommandResult:
    skills = scan_installed_skills()
    if not skills:
        return CommandResult(handled=True, text="未安装任何 skill 包。")
    lines = [f"- /{name} — {desc}" for name, desc in skills]
    return CommandResult(handled=True, text="已安装 skill 包:\n" + "\n".join(lines))
