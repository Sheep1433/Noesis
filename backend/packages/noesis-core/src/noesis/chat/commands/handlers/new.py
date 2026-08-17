"""/new —— 开新会话（重绑通道 binding 到新 session，旧会话保留可追溯）。

对齐 hermes gateway 的 ``/new``（别名 ``/reset``）与 Claude Code 的 ``/clear``：
换新 session ID 从头开始，旧 session 不软删、可回溯。

通道差异（在 ``registry`` 层用 ``channels`` 声明）：
- Web 通道不注册 ``/new``——Web 已有「新对话」按钮，命令入口冗余。
- Telegram / 飞书等无 UI 通道才需要：``ChannelBinding`` 绑定单 session，
  无 UI 开新对话，``/new`` 通过重绑 binding 到新 session 实现开新会话。

旧 session 不调任何软删/删除——消息原样留存（对齐 hermes ``end_session`` 不删）。
"""
from __future__ import annotations

from noesis.chat.commands.result import CommandResult
from noesis.chat.commands.registry import command
from noesis.chat.commands.runtime import get_session_factory
from noesis.chat.delivery.channels import InboundMessage, channel_bindings
from noesis.runtime.logging import logger


@command("new", description="开始新会话（旧会话保留可追溯）", channels=("telegram", "feishu"))
async def new_cmd(ev: InboundMessage) -> CommandResult:
    binding = channel_bindings.resolve(ev.channel_type, ev.external_chat_id, ev.thread_id)
    if binding is None:
        return CommandResult(
            handled=True,
            text="当前通道未配对，请先在网页设置中绑定通讯通道后再使用 /new。",
        )

    factory = get_session_factory()
    if factory is None:
        return CommandResult(handled=True, text="新会话创建在当前环境不可用。")

    try:
        new_session_id = await factory(binding.user_id)
    except Exception:  # noqa: BLE001 —— 工厂内部 DB 失败需给出可恢复提示
        logger.exception("new command: create_session failed user_id={}", binding.user_id)
        return CommandResult(handled=True, text="创建新会话失败，请稍后重试。")

    old_session_id = binding.session_id
    channel_bindings.put(binding.rebind_to(new_session_id))
    old_hint = f"{old_session_id[:8]}…" if old_session_id else "原会话"
    return CommandResult(
        handled=True,
        text=f"已开始新会话。\n旧会话（{old_hint}）保留，可在网页历史中查看。",
    )
