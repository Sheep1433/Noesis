"""飞书企业自建应用 WebSocket 运行时。"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import lark_oapi as lark
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

from noesis.config.env import MessagingConfig
from noesis.runtime.logging import logger
from noesis.domain.chat.delivery.channel_health import channel_health
from noesis.domain.chat.delivery.channels import InboundRouteResult, route_inbound
from noesis.domain.chat.delivery.feishu.adapter import FeishuChannelAdapter
from noesis.domain.chat.delivery.feishu.client import FeishuBotClient, mask_app_id
from noesis.domain.chat.delivery.feishu.stream_out import FeishuOutbound
from noesis.domain.chat.hitl.pending import pending_hitl
from noesis_server.services.channel_run_service import resume_channel_hitl, run_channel_agent
from noesis_server.services.messaging_channel_service import MessagingChannelService, RuntimeChannelConfig

_supervisor: asyncio.Task | None = None
_stop = asyncio.Event()
_active_thread: threading.Thread | None = None
_active = False
_main_loop: asyncio.AbstractEventLoop | None = None


@dataclass
class _HitlPrompt:
    token: str
    user_id: str
    session_id: str
    chat_id: str
    interrupt_id: str
    action_count: int
    expires_at: float


_hitl_prompts: dict[str, _HitlPrompt] = {}
_HITL_PROMPT_MAX_ITEMS = 4096


def _evict_expired_hitl_prompts(*, now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    for token, prompt in tuple(_hitl_prompts.items()):
        if prompt.expires_at <= current:
            _hitl_prompts.pop(token, None)


def _store_hitl_prompt(prompt: _HitlPrompt) -> None:
    _evict_expired_hitl_prompts()
    while len(_hitl_prompts) >= _HITL_PROMPT_MAX_ITEMS:
        oldest_token = min(
            _hitl_prompts,
            key=lambda token: _hitl_prompts[token].expires_at,
        )
        _hitl_prompts.pop(oldest_token, None)
    _hitl_prompts[prompt.token] = prompt


def _as_dict(value: Any) -> dict[str, Any]:
    try:
        raw = lark.JSON.marshal(value)
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _parse_card_action(raw: dict[str, Any]) -> tuple[str, str, str]:
    event = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value = action.get("value") if isinstance(action.get("value"), dict) else {}
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    return (
        str(value.get("token") or ""),
        str(value.get("decision") or ""),
        str(operator.get("open_id") or ""),
    )


def _schedule(coro: Any) -> None:
    loop = _main_loop
    if loop is None or loop.is_closed():
        return
    loop.call_soon_threadsafe(lambda: asyncio.create_task(coro))


def _hitl_card(token: str, payload: dict[str, Any]) -> dict[str, Any]:
    actions = list(payload.get("action_requests") or [])
    names = [str(item.get("name") or item.get("tool") or "操作") for item in actions if isinstance(item, dict)]
    summary = "、".join(names[:5]) or "敏感操作"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "orange", "title": {"tag": "plain_text", "content": "Noesis 等待审批"}},
        "elements": [
            {"tag": "markdown", "content": f"即将执行：**{summary}**\n请确认是否继续。"},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "批准"}, "type": "primary", "value": {"token": token, "decision": "approve"}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "拒绝"}, "type": "danger", "value": {"token": token, "decision": "reject"}},
            ]},
        ],
    }


async def _deliver_after_result(cfg: RuntimeChannelConfig, client: FeishuBotClient, chat_id: str, session_id: str, outbound: FeishuOutbound | None, result: Any) -> None:
    if result.hitl_pending and result.hitl_payload:
        payload = dict(result.hitl_payload)
        if str(payload.get("kind") or "approval") == "clarification":
            await client.send_text(chat_id, "需要你补充信息，请直接回复下一条消息。")
            return
        token = secrets.token_urlsafe(18)
        actions = list(payload.get("action_requests") or [])
        _store_hitl_prompt(_HitlPrompt(
            token=token, user_id=str(cfg.user_id), session_id=session_id, chat_id=chat_id,
            interrupt_id=str(payload.get("interrupt_id") or ""), action_count=max(1, len(actions)),
            expires_at=time.monotonic() + 86400,
        ))
        await client.send_card(chat_id, _hitl_card(token, payload))
    elif outbound is not None and not outbound.sent_any and result.plain_text:
        await outbound.deliver_final(result.plain_text)


async def _try_clarification(cfg: RuntimeChannelConfig, client: FeishuBotClient, binding: Any, chat_id: str, text: str) -> bool:
    pending = pending_hitl.get(binding.session_id)
    if pending is None or pending.kind != "clarification" or pending.user_id != str(binding.user_id):
        return False
    decisions = [{"type": "respond", "message": text} for _ in range(max(1, len(pending.action_requests or [])))]
    outbound = FeishuOutbound(client, chat_id)
    result = await resume_channel_hitl(
        user_id=binding.user_id, session_id=binding.session_id, interrupt_id=pending.interrupt_id,
        decisions=decisions, grant_scope=None, origin="feishu", outbound=outbound,
    )
    await _deliver_after_result(cfg, client, chat_id, binding.session_id, outbound, result)
    return True


async def _handle_message(
    cfg: RuntimeChannelConfig,
    client: FeishuBotClient,
    adapter: FeishuChannelAdapter,
    raw: dict[str, Any],
    *,
    normalized: Any = None,
    routed: InboundRouteResult | None = None,
) -> None:
    inbound = normalized or await adapter.normalize_inbound(raw)
    if inbound is None:
        return
    channel_health.report_activity(cfg.user_id, cfg.channel_id, "inbound", "received")
    routed = routed or route_inbound(inbound)
    chat_id = str(inbound.raw.get("reply_chat_id") or cfg.pairing_chat_id or "")
    if not routed.ok or routed.binding is None:
        channel_health.report_activity(cfg.user_id, cfg.channel_id, "inbound", "rejected_unpaired")
        if chat_id:
            await client.send_text(chat_id, f"此账号尚未与 Noesis 配对。请在设置中填写发送者 Open ID：{inbound.external_chat_id}")
        return
    binding = routed.binding
    if await _try_clarification(cfg, client, binding, chat_id, inbound.text):
        return
    pending = pending_hitl.get(binding.session_id)
    if pending is not None and pending.user_id == str(binding.user_id):
        await client.send_text(chat_id, "当前有待审批操作，请先处理上方审批卡片。")
        return
    session_id = str(uuid.uuid4()) if cfg.session_strategy == "new_per_message" else binding.session_id
    outbound = FeishuOutbound(client, chat_id, reply_message_id=inbound.external_message_id) if cfg.delivery_preference == "reply" else None
    try:
        result = await run_channel_agent(
            user_id=binding.user_id, session_id=session_id, query=inbound.text,
            qa_type=cfg.default_qa_type, origin="feishu", external_message_id=inbound.external_message_id,
            channel_type="feishu", outbound=outbound,
        )
        channel_health.report_activity(cfg.user_id, cfg.channel_id, "inbound", "succeeded")
        await _deliver_after_result(cfg, client, chat_id, session_id, outbound, result)
    except Exception:
        channel_health.report_activity(cfg.user_id, cfg.channel_id, "inbound", "failed")
        logger.exception("feishu inbound failed user={} channel={}", cfg.user_id, cfg.channel_id)
        if chat_id:
            await client.send_text(chat_id, "处理失败，请稍后重试或到网页查看。")


async def _handle_card(cfg: RuntimeChannelConfig, client: FeishuBotClient, raw: dict[str, Any]) -> None:
    token, decision, open_id = _parse_card_action(raw)
    prompt = _hitl_prompts.get(token)
    if prompt is None:
        return
    if prompt.expires_at <= time.monotonic():
        _hitl_prompts.pop(token, None)
        return
    if open_id != str(cfg.pairing_user_id or ""):
        return
    _hitl_prompts.pop(token, None)
    decisions = [{"type": "approve" if decision == "approve" else "reject"} for _ in range(prompt.action_count)]
    outbound = FeishuOutbound(client, prompt.chat_id)
    result = await resume_channel_hitl(
        user_id=prompt.user_id, session_id=prompt.session_id, interrupt_id=prompt.interrupt_id,
        decisions=decisions, grant_scope=None, origin="feishu", outbound=outbound,
    )
    await _deliver_after_result(cfg, client, prompt.chat_id, prompt.session_id, outbound, result)


def _config_for_binding(user_id: str | int, open_id: str) -> RuntimeChannelConfig | None:
    return next(
        (
            cfg
            for cfg in MessagingChannelService.iter_enabled_runtime("feishu", user_id=user_id)
            if cfg.pairing_user_id == open_id
        ),
        None,
    )


async def _dispatch_message(client: FeishuBotClient, adapter: FeishuChannelAdapter, raw: dict[str, Any]) -> None:
    inbound = await adapter.normalize_inbound(raw)
    if inbound is None:
        return
    routed = route_inbound(inbound)
    cfg = _config_for_binding(routed.binding.user_id, inbound.external_chat_id) if routed.ok and routed.binding else None
    if cfg is None:
        chat_id = str(inbound.raw.get("reply_chat_id") or "")
        if chat_id:
            await client.send_text(chat_id, f"此账号尚未与 Noesis 配对。请在设置中填写发送者 Open ID：{inbound.external_chat_id}")
        return
    await _handle_message(
        cfg,
        client,
        adapter,
        raw,
        normalized=inbound,
        routed=routed,
    )


async def _dispatch_card(client: FeishuBotClient, raw: dict[str, Any]) -> None:
    token, _, open_id = _parse_card_action(raw)
    prompt = _hitl_prompts.get(token)
    cfg = _config_for_binding(prompt.user_id, open_id) if prompt else None
    if cfg is not None:
        await _handle_card(cfg, client, raw)


def _report_all(status: str, message: str, *, error_category: str | None = None) -> None:
    for cfg in MessagingChannelService.iter_enabled_runtime("feishu"):
        channel_health.report_status(cfg.user_id, cfg.channel_id, status, message, error_category=error_category)


def _start_sdk_thread() -> threading.Thread:
    app_id = MessagingConfig.feishu_app_id
    app_secret = MessagingConfig.feishu_app_secret
    client = FeishuBotClient(app_id, app_secret)
    adapter = FeishuChannelAdapter(client)

    def on_message(data: Any) -> None:
        if _active:
            _schedule(_dispatch_message(client, adapter, _as_dict(data)))

    def on_card(data: Any) -> P2CardActionTriggerResponse:
        if _active:
            _schedule(_dispatch_card(client, _as_dict(data)))
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已收到，正在处理"}})

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(on_card)
        .build()
    )
    ws_client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.WARNING)

    def run() -> None:
        try:
            _report_all("healthy", "飞书连接正常")
            ws_client.start()
        except Exception:
            _report_all("unavailable", "飞书连接失败", error_category="connection")
            logger.exception("feishu websocket stopped app={}", mask_app_id(app_id))

    return threading.Thread(target=run, name="feishu-ws", daemon=True)


async def _supervisor_loop() -> None:
    global _active_thread, _active
    while not _stop.is_set():
        _evict_expired_hitl_prompts()
        configured = bool(MessagingConfig.feishu_app_id and MessagingConfig.feishu_app_secret)
        if configured and (not _active_thread or not _active_thread.is_alive()):
            _active = True
            _active_thread = _start_sdk_thread()
            _active_thread.start()
        elif not configured:
            _active = False
            _report_all("unavailable", "飞书服务暂不可用", error_category="configuration")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass


def start_feishu_runtime() -> None:
    global _supervisor, _main_loop
    _main_loop = asyncio.get_running_loop()
    _stop.clear()
    if not MessagingConfig.feishu_runtime_enabled:
        logger.info("feishu runtime disabled (messaging.feishu_runtime_enabled=false)")
        return
    if _supervisor is None or _supervisor.done():
        _supervisor = asyncio.create_task(_supervisor_loop(), name="feishu-runtime")


async def stop_feishu_runtime() -> None:
    global _supervisor, _active
    _active = False
    _stop.set()
    if _supervisor is not None:
        _supervisor.cancel()
        try:
            await _supervisor
        except (asyncio.CancelledError, Exception):
            pass
    _supervisor = None
    _hitl_prompts.clear()
