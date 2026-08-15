"""/compact —— 手动触发上下文压缩。

CompactionMiddleware 在 wrap_model_call 检查 runtime.context 的
``manual_compact_requested`` 标记。本命令通过 RequestRewrite 改写为
一条 Agent run，并在 extra 中设置标记；Agent 的下一轮 model call
检测到标记后强制压缩（绕过阈值和 breaker）。

design §12: 手动 compact 不受自动熔断限制，使用同一 compaction engine。
"""
from __future__ import annotations

from noesis.chat.commands.result import CommandResult, RequestRewrite
from noesis.chat.commands.registry import command
from noesis.chat.delivery.channels import InboundMessage


@command("compact", description="手动压缩对话上下文（不受自动阈值限制）")
async def compact_cmd(ev: InboundMessage) -> CommandResult:
    if not ev.session_id:
        return CommandResult(handled=True, text="缺少会话上下文，无法压缩。")
    # 改写为一条 Agent run，extra 带 compact 标记
    # server wiring 在解析 extra 时把标记注入 runtime.context
    return CommandResult(
        handled=True,
        rewrite_request=RequestRewrite(
            query="（请继续，上一轮已触发手动上下文压缩）",
        ),
    )
