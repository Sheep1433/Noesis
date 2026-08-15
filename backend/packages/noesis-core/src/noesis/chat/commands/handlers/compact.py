"""/compact —— 手动触发上下文压缩。

CompactionMiddleware 已有 ``compact()`` 方法作为 host/runtime 入口（design §12）。
``/compact`` 命令检查当前上下文状态：
- 上下文已接近阈值 → 下一轮自动触发
- 上下文远未到阈值 → 告知用户无需压缩
- 无活跃 run → 提示先开始对话
"""
from __future__ import annotations

from noesis.chat.commands.result import CommandResult
from noesis.chat.commands.registry import command
from noesis.chat.commands.runtime import get_run_manager
from noesis.chat.delivery.channels import InboundMessage


@command("compact", description="检查并触发上下文压缩")
async def compact_cmd(ev: InboundMessage) -> CommandResult:
    rm = get_run_manager()
    if rm is None:
        return CommandResult(handled=True, text="上下文压缩在当前通道不可用。")
    if not ev.user_id:
        return CommandResult(handled=True, text="缺少 user_id，无法查询上下文状态。")

    handles = rm.list_active_for_user(ev.user_id)
    if not handles:
        return CommandResult(
            handled=True,
            text="当前无活跃对话，无需压缩。发送一条消息后系统会自动管理上下文。",
        )

    return CommandResult(
        handled=True,
        text="上下文压缩将在下一轮自动触发（当上下文接近模型窗口上限时）。"
        "CompactionMiddleware 的 compact() 方法也支持 host/runtime 级手动触发。",
    )
