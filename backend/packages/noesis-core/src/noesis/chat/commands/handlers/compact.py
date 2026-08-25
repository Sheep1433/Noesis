"""/compact —— 在当前对话空闲时手动压缩较早历史。"""
from __future__ import annotations

from noesis.chat.commands.result import CommandResult
from noesis.chat.commands.registry import command
from noesis.chat.commands.runtime import get_compaction_provider
from noesis.chat.delivery.channels import InboundMessage, channel_bindings
from noesis.runtime.logging import logger


def _session_id(ev: InboundMessage) -> str | None:
    if ev.channel_type == "web":
        return ev.external_chat_id or None
    binding = channel_bindings.resolve(ev.channel_type, ev.external_chat_id, ev.thread_id)
    return binding.session_id if binding is not None else None


@command("compact", description="手动压缩较早的对话内容")
async def compact_cmd(ev: InboundMessage) -> CommandResult:
    if not ev.user_id:
        return CommandResult(handled=True, text="当前无法识别对话，请稍后重试。")

    provider = get_compaction_provider()
    session_id = _session_id(ev)
    if provider is None or session_id is None:
        return CommandResult(handled=True, text="当前暂时无法压缩对话，请稍后重试。")

    try:
        outcome = await provider(session_id, ev.user_id, ev.command_args() or None)
    except Exception:  # noqa: BLE001 —— 命令边界统一返回可恢复提示
        logger.exception("compact command failed session_id={} user_id={}", session_id, ev.user_id)
        return CommandResult(handled=True, text="压缩对话失败，请稍后重试。")

    messages = {
        "no_history": "当前没有可压缩的较早对话内容。",
        "busy": "当前对话正在处理中，请等本轮完成后再试。",
        "not_found": "找不到当前对话，请刷新后重试。",
        "disabled": "当前暂时无法压缩对话，请稍后重试。",
    }
    if outcome.status == "completed":
        saved_messages = max(0, outcome.pre_message_count - outcome.post_message_count)
        return CommandResult(
            handled=True,
            text=f"已压缩 {saved_messages} 条较早的对话内容，后续对话会继续使用更新后的上下文。",
        )
    return CommandResult(
        handled=True,
        text=messages.get(outcome.status, "压缩对话失败，请稍后重试。"),
    )
