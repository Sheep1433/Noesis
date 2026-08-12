"""/status —— 查看用户当前活跃 run 与是否 HITL 挂起。

run_manager 通过命令层 runtime 注入（server wiring）；未注入时（如 CLI）graceful
降级为「状态查询不可用」。
"""
from __future__ import annotations

from noesis.chat.commands.result import CommandResult
from noesis.chat.commands.registry import command
from noesis.chat.commands.runtime import get_run_manager
from noesis.chat.delivery.channels import InboundMessage
from noesis.chat.runs.models import RunStatus


@command("status", description="查看当前活跃 run 与 HITL 挂起状态")
async def status_cmd(ev: InboundMessage) -> CommandResult:
    rm = get_run_manager()
    if rm is None:
        return CommandResult(handled=True, text="状态查询在当前通道不可用。")
    if not ev.user_id:
        return CommandResult(handled=True, text="缺少 user_id，无法查询 run 状态。")

    handles = rm.list_active_for_user(ev.user_id)
    if not handles:
        return CommandResult(handled=True, text="当前无活跃 run。")

    lines = []
    for h in handles:
        hitl = "  ⏳ HITL 待审批" if h.status == RunStatus.HITL_PENDING else ""
        lines.append(f"- {h.run_id} [{h.status.value}]{hitl}")
    return CommandResult(handled=True, text="活跃 run:\n" + "\n".join(lines))
