"""Telegram long-poll 运行时：按启用通道起 Task，入站编排 + 出站 sendMessage。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set

from domain.chat.hitl.pending import pending_hitl
from common.logging import logger
from config.env import MessagingConfig
from domain.chat.delivery.channels import route_inbound
from domain.chat.delivery.telegram.adapter import TelegramChannelAdapter
from domain.chat.delivery.telegram.client import TelegramBotClient, mask_bot_token
from domain.chat.delivery.telegram.hitl_prompt import (
    allow_session_grant_for_actions,
    build_approval_keyboard,
    decisions_for_op,
    format_hitl_card_text,
    parse_hitl_callback_data,
    register_hitl_prompt,
    telegram_hitl_prompts,
)
from domain.chat.delivery.telegram.stream_out import TelegramOutbound, deliver_final_markdown
from services.channel_run_service import resume_channel_hitl, run_channel_agent
from services.messaging_channel_service import (
    MessagingChannelService,
    RuntimeChannelConfig,
)

_PAIRING_HINT = (
    "此聊天尚未与 Noesis 配对。\n"
    "请打开网页「设置 → 通讯通道」，将配对 Chat ID 填为：\n"
    "{chat_id}"
)

_tasks: Dict[str, asyncio.Task] = {}
_supervisor: Optional[asyncio.Task] = None
_stop = asyncio.Event()


def _worker_key(cfg: RuntimeChannelConfig) -> str:
    return f"{cfg.user_id}:{cfg.channel_id}"


async def _deliver_hitl_card(
    client: TelegramBotClient,
    *,
    chat_id: str,
    session_id: str,
    user_id: str | int,
    payload: Dict[str, Any],
) -> None:
    text = format_hitl_card_text(payload)
    kind = str(payload.get("kind") or "approval")
    actions = list(payload.get("action_requests") or [])
    prompt = register_hitl_prompt(
        session_id=session_id,
        user_id=user_id,
        chat_id=chat_id,
        payload=payload,
    )
    reply_markup = None
    if kind != "clarification":
        reply_markup = build_approval_keyboard(
            prompt.token,
            allow_session_grant=allow_session_grant_for_actions(actions),
        )
    try:
        result = await client.send_message(
            chat_id, text, reply_markup=reply_markup
        )
        mid = result.get("message_id") if isinstance(result, dict) else None
        if mid is not None:
            prompt.message_id = int(mid)
            telegram_hitl_prompts.put(prompt)
    except Exception:
        logger.exception(
            "telegram hitl card send failed chat_id={} session_id={}",
            chat_id,
            session_id,
        )


async def _after_channel_result(
    client: TelegramBotClient,
    chat_id: str,
    binding_user_id: str | int,
    binding_session_id: str,
    outbound: TelegramOutbound,
    result: Any,
) -> None:
    if result.hitl_pending and result.hitl_payload:
        await _deliver_hitl_card(
            client,
            chat_id=chat_id,
            session_id=binding_session_id,
            user_id=binding_user_id,
            payload=result.hitl_payload,
        )
        return
    if not outbound.sent_any and result.plain_text:
        await deliver_final_markdown(client, chat_id, result.plain_text)


async def _handle_callback_query(
    cfg: RuntimeChannelConfig,
    client: TelegramBotClient,
    update: dict,
) -> None:
    cq = update.get("callback_query")
    if not isinstance(cq, dict):
        return
    cq_id = str(cq.get("id") or "")
    data = str(cq.get("data") or "")
    parsed = parse_hitl_callback_data(data)
    msg = cq.get("message") if isinstance(cq.get("message"), dict) else {}
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    message_id = msg.get("message_id")

    if parsed is None:
        if cq_id:
            try:
                await client.answer_callback_query(cq_id, text="无效操作")
            except Exception:
                pass
        return

    token, op = parsed
    prompt = telegram_hitl_prompts.get(token)
    if prompt is None:
        if cq_id:
            try:
                await client.answer_callback_query(
                    cq_id, text="该审批已失效或已处理"
                )
            except Exception:
                pass
        return

    if str(prompt.user_id) != str(cfg.user_id):
        if cq_id:
            try:
                await client.answer_callback_query(cq_id, text="无权操作")
            except Exception:
                pass
        return

    if chat_id and prompt.chat_id and str(chat_id) != str(prompt.chat_id):
        if cq_id:
            try:
                await client.answer_callback_query(cq_id, text="聊天不匹配")
            except Exception:
                pass
        return

    ack = {"a": "已批准，继续执行…", "r": "已拒绝", "s": "本会话放行，继续…"}[op]
    if cq_id:
        try:
            await client.answer_callback_query(cq_id, text=ack)
        except Exception:
            pass

    # 先 pop，避免重复点击
    telegram_hitl_prompts.pop(token)
    mid = int(message_id) if message_id is not None else prompt.message_id
    if mid is not None and chat_id:
        try:
            await client.edit_message_reply_markup(chat_id, mid, reply_markup=None)
        except Exception:
            pass
        try:
            suffix = {
                "a": "\n\n—— 已批准 ——",
                "r": "\n\n—— 已拒绝 ——",
                "s": "\n\n—— 本会话放行 ——",
            }[op]
            # 保留原文并标注状态（取不到原文则跳过）
            base = format_hitl_card_text(
                {
                    "kind": prompt.kind,
                    "action_requests": prompt.action_requests,
                }
            )
            await client.edit_message_text(chat_id, mid, (base + suffix)[:4096])
        except Exception:
            pass

    decisions, grant_scope = decisions_for_op(op, len(prompt.action_requests))
    try:
        outbound = TelegramOutbound(client, chat_id or prompt.chat_id)
        result = await resume_channel_hitl(
            user_id=prompt.user_id,
            session_id=prompt.session_id,
            interrupt_id=prompt.interrupt_id,
            decisions=decisions,
            grant_scope=grant_scope,
            origin="telegram",
            outbound=outbound,
        )
        await _after_channel_result(
            client,
            chat_id or prompt.chat_id,
            prompt.user_id,
            prompt.session_id,
            outbound,
            result,
        )
    except Exception:
        logger.exception(
            "telegram hitl resume failed session_id={} token={}",
            prompt.session_id,
            token,
        )
        try:
            await client.send_message(
                chat_id or prompt.chat_id,
                "审批后续执行失败，请稍后重试或到网页查看。",
            )
        except Exception:
            pass


async def _try_pending_clarification_reply(
    *,
    client: TelegramBotClient,
    binding: Any,
    chat_id: str,
    text: str,
) -> bool:
    """若本 session 有 clarification pending，把用户文字当 respond。"""
    pending = pending_hitl.get(binding.session_id)
    if pending is None or pending.kind != "clarification":
        return False
    if pending.user_id != str(binding.user_id):
        return False
    actions = list(pending.action_requests or [])
    n = max(1, len(actions))
    decisions = [{"type": "respond", "message": text} for _ in range(n)]
    telegram_hitl_prompts.clear_session(binding.session_id)
    try:
        outbound = TelegramOutbound(client, chat_id)
        result = await resume_channel_hitl(
            user_id=binding.user_id,
            session_id=binding.session_id,
            interrupt_id=pending.interrupt_id,
            decisions=decisions,
            grant_scope=None,
            origin="telegram",
            outbound=outbound,
        )
        await _after_channel_result(
            client,
            chat_id,
            binding.user_id,
            binding.session_id,
            outbound,
            result,
        )
    except Exception:
        logger.exception(
            "telegram clarification resume failed session_id={}",
            binding.session_id,
        )
        await client.send_message(chat_id, "澄清回复处理失败，请稍后重试。")
    return True


async def _handle_message(
    cfg: RuntimeChannelConfig,
    client: TelegramBotClient,
    adapter: TelegramChannelAdapter,
    update: dict,
) -> None:
    inbound = await adapter.normalize_inbound(update)
    if inbound is None:
        return

    MessagingChannelService.iter_enabled_runtime("telegram", user_id=cfg.user_id)
    routed = route_inbound(inbound)
    chat_id = inbound.external_chat_id

    if not routed.ok or routed.binding is None:
        hint = _PAIRING_HINT.format(chat_id=chat_id)
        try:
            await client.send_message(chat_id, hint)
        except Exception:
            logger.exception(
                "telegram unpaired hint failed chat_id={} bot={}",
                chat_id,
                mask_bot_token(cfg.bot_token),
            )
        return

    binding = routed.binding
    if str(binding.user_id) != str(cfg.user_id):
        logger.warning(
            "telegram binding user mismatch poll_user={} binding_user={}",
            cfg.user_id,
            binding.user_id,
        )
        return

    # 审批等待中：提醒用按钮；澄清等待中：文字即回答
    pending = pending_hitl.get(binding.session_id)
    if pending is not None and pending.user_id == str(binding.user_id):
        if pending.kind == "clarification":
            if await _try_pending_clarification_reply(
                client=client,
                binding=binding,
                chat_id=chat_id,
                text=inbound.text,
            ):
                return
        else:
            try:
                await client.send_message(
                    chat_id,
                    "当前有待审批操作，请点击上方「批准 / 拒绝」按钮，或到网页确认。",
                )
            except Exception:
                pass
            return

    try:
        outbound = TelegramOutbound(client, chat_id)
        result = await run_channel_agent(
            user_id=binding.user_id,
            session_id=binding.session_id,
            query=inbound.text,
            qa_type=cfg.default_qa_type,
            origin="telegram",
            external_message_id=inbound.external_message_id,
            channel_type="telegram",
            outbound=outbound,
        )
        await _after_channel_result(
            client,
            chat_id,
            binding.user_id,
            binding.session_id,
            outbound,
            result,
        )
    except Exception:
        logger.exception(
            "telegram handle inbound failed user={} chat_id={}",
            cfg.user_id,
            chat_id,
        )
        try:
            await client.send_message(chat_id, "处理失败，请稍后重试或到网页查看。")
        except Exception:
            pass


async def _handle_update(
    cfg: RuntimeChannelConfig,
    client: TelegramBotClient,
    adapter: TelegramChannelAdapter,
    update: dict,
) -> None:
    if isinstance(update.get("callback_query"), dict):
        await _handle_callback_query(cfg, client, update)
        return
    await _handle_message(cfg, client, adapter, update)


async def _poll_loop(cfg: RuntimeChannelConfig) -> None:
    timeout = int(MessagingConfig.telegram_poll_timeout_seconds)
    client = TelegramBotClient(cfg.bot_token, timeout=float(timeout) + 15.0)
    adapter = TelegramChannelAdapter(client=client)
    offset: Optional[int] = None
    masked = mask_bot_token(cfg.bot_token)
    logger.info(
        "telegram poll start user={} channel={} bot={}",
        cfg.user_id,
        cfg.channel_id,
        masked,
    )
    try:
        await adapter.start()
        while not _stop.is_set():
            try:
                updates = await client.get_updates(
                    offset=offset,
                    timeout=timeout,
                    allowed_updates=["message", "callback_query"],
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "telegram getUpdates error bot={} err={}；5s 后重试",
                    masked,
                    type(exc).__name__,
                )
                try:
                    await asyncio.wait_for(_stop.wait(), timeout=5.0)
                    break
                except asyncio.TimeoutError:
                    continue

            for upd in updates:
                if _stop.is_set():
                    break
                uid = upd.get("update_id")
                if isinstance(uid, int):
                    offset = uid + 1
                try:
                    await _handle_update(cfg, client, adapter, upd)
                except Exception:
                    logger.exception("telegram update handle error bot={}", masked)
    finally:
        await adapter.stop()
        await client.aclose()
        logger.info(
            "telegram poll stop user={} channel={} bot={}",
            cfg.user_id,
            cfg.channel_id,
            masked,
        )


async def _reconcile_workers() -> None:
    """按当前磁盘配置启停 poll Task。"""
    if not MessagingConfig.telegram_runtime_enabled:
        for key, task in list(_tasks.items()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            _tasks.pop(key, None)
        return

    MessagingChannelService.sync_all_bindings()
    cfgs = MessagingChannelService.iter_enabled_runtime("telegram")
    wanted: Set[str] = set()
    for cfg in cfgs:
        key = _worker_key(cfg)
        wanted.add(key)
        existing = _tasks.get(key)
        if existing is not None and not existing.done():
            continue
        _tasks[key] = asyncio.create_task(
            _poll_loop(cfg), name=f"tg-poll-{key}"
        )

    for key, task in list(_tasks.items()):
        if key in wanted:
            continue
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        _tasks.pop(key, None)


async def _supervisor_loop() -> None:
    logger.info(
        "telegram runtime supervisor started enabled={}",
        MessagingConfig.telegram_runtime_enabled,
    )
    while not _stop.is_set():
        try:
            await _reconcile_workers()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("telegram supervisor reconcile error")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=30.0)
            break
        except asyncio.TimeoutError:
            continue
    # drain
    for key, task in list(_tasks.items()):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        _tasks.pop(key, None)


def start_telegram_runtime() -> None:
    global _supervisor
    _stop.clear()
    if not MessagingConfig.telegram_runtime_enabled:
        logger.info("telegram runtime disabled (messaging.telegram_runtime_enabled=false)")
        return
    if _supervisor is not None and not _supervisor.done():
        return
    _supervisor = asyncio.create_task(_supervisor_loop(), name="telegram-runtime")


async def stop_telegram_runtime() -> None:
    global _supervisor
    _stop.set()
    if _supervisor is None:
        for key, task in list(_tasks.items()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            _tasks.pop(key, None)
        return
    _supervisor.cancel()
    try:
        await _supervisor
    except (asyncio.CancelledError, Exception):
        pass
    _supervisor = None
