"""轻量 run 编排：组 sinks、启 agent 流、向 RunEventBus 发布；含 RunLifecycle。"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional

from domain.chat.delivery.bus import RunEventBus
from domain.chat.delivery.events import RunEvent, RunOrigin, RunStarted
from domain.chat.delivery.sse import LcEventMapper, encode_filtered, iter_sse_from_bus
from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    MemoryStreamBridge,
    StreamBridgeError,
    iter_bridge_events,
)
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge

OnEvents = Callable[[List[RunEvent]], Awaitable[None]]


class CancelReason(str, Enum):
    USER_STOP = "user_stop"
    DISCONNECT = "disconnect"
    CHANNEL_STOP = "channel_stop"
    SYSTEM = "system"


class RunLifecycle:
    """进程内 active run 注册与 cancel 语义（对齐现网 stop / 断连）。"""

    def __init__(self) -> None:
        self._streams: Dict[str, Dict[str, Any]] = {}

    def register(self, session_id: str, state: Dict[str, Any]) -> None:
        self._streams[session_id] = state

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._streams.get(session_id)

    def pop(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._streams.pop(session_id, None)

    def notify_cancel(self, session_id: str, reason: CancelReason) -> Optional[Dict[str, Any]]:
        state = self._streams.get(session_id)
        if state is None:
            return None
        ctx = state.get("ctx")
        if isinstance(ctx, dict):
            if reason == CancelReason.USER_STOP:
                ctx["user_stopped"] = True
            ctx["cancel_reason"] = reason.value
        state["cancel_reason"] = reason.value
        return state


# 与 QaService._active_streams 过渡期可并存，逐步迁入。
run_lifecycle = RunLifecycle()


def bridge_run_id(session_id: str, assistant_message_id: str) -> str:
    return f"{session_id}:{assistant_message_id}"


class RunOrchestrator:
    """
    在现有 agent generator + LangGraphSseBridge 之上提供 Fan-out 接缝。

    不依赖 noesis_runtime 搬家。raw 经 MemoryStreamBridge（无 heartbeat）→
    LcEventMapper → RunEventBus；SSE 订阅并注入 keepalive。
    """

    def __init__(self, bus: Optional[RunEventBus] = None) -> None:
        self.bus = bus or RunEventBus()

    async def stream_sse(
        self,
        agent_generator: AsyncGenerator[Any, None],
        *,
        bridge: LangGraphSseBridge,
        builder: AssistantMessageBuilder,
        ctx: Dict[str, Any],
        session_id: str,
        keepalive_seconds: float,
        origin: RunOrigin = "web",
        langfuse_context: Optional[Any] = None,
        on_events: Optional[OnEvents] = None,
    ) -> AsyncGenerator[str, None]:
        run_id = bridge_run_id(session_id, bridge.assistant_message_id)
        mapper = LcEventMapper(bridge)
        sse_q = self.bus.subscribe_queue(run_id)

        await self.bus.publish(
            run_id,
            RunStarted(
                run_id=run_id,
                session_id=session_id,
                assistant_message_id=bridge.assistant_message_id,
                origin=origin,
            ),
        )

        producer = asyncio.create_task(
            self._produce(
                agent_generator,
                run_id=run_id,
                mapper=mapper,
                builder=builder,
                ctx=ctx,
                langfuse_context=langfuse_context,
                on_events=on_events,
            )
        )
        try:
            async for line in iter_sse_from_bus(
                self.bus,
                run_id,
                keepalive_seconds=keepalive_seconds,
                queue=sse_q,
            ):
                yield line
            await producer
        finally:
            if not producer.done():
                producer.cancel()
                try:
                    await producer
                except (asyncio.CancelledError, Exception):
                    pass
            self.bus.unsubscribe_queue(run_id, sse_q)
            self.bus.cleanup(run_id)

    async def run_headless(
        self,
        agent_generator: AsyncGenerator[Any, None],
        *,
        bridge: LangGraphSseBridge,
        builder: AssistantMessageBuilder,
        ctx: Dict[str, Any],
        session_id: str,
        origin: RunOrigin = "telegram",
        langfuse_context: Optional[Any] = None,
        on_events: Optional[OnEvents] = None,
        finish_reason: Optional[str] = None,
    ) -> None:
        """无 SSE 订阅者的跑次：仍经 Bus + PersistSink on_events，不编码 keepalive。"""
        run_id = bridge_run_id(session_id, bridge.assistant_message_id)
        mapper = LcEventMapper(bridge)
        await self.bus.publish(
            run_id,
            RunStarted(
                run_id=run_id,
                session_id=session_id,
                assistant_message_id=bridge.assistant_message_id,
                origin=origin,
            ),
        )
        try:
            await self._produce(
                agent_generator,
                run_id=run_id,
                mapper=mapper,
                builder=builder,
                ctx=ctx,
                langfuse_context=langfuse_context,
                on_events=on_events,
            )
            fr = finish_reason
            if fr is None and ctx.get("user_stopped"):
                fr = "stopped"
            events = mapper.finalize(finish_reason=fr)
            if on_events is not None and events:
                await on_events(events)
            for ev in events:
                await self.bus.publish(run_id, ev)
        finally:
            self.bus.cleanup(run_id)

    def finalize_sse(
        self,
        bridge: LangGraphSseBridge,
        *,
        finish_reason: Optional[str] = None,
    ) -> List[str]:
        """流结束后补 finish/[DONE]（对齐 bridge.finalize）。"""
        mapper = LcEventMapper(bridge)
        events = mapper.finalize(finish_reason=finish_reason)
        lines: List[str] = []
        for ev in events:
            lines.extend(encode_filtered(ev))
        return lines

    async def _produce(
        self,
        agent_generator: AsyncGenerator[Any, None],
        *,
        run_id: str,
        mapper: LcEventMapper,
        builder: AssistantMessageBuilder,
        ctx: Dict[str, Any],
        langfuse_context: Optional[Any],
        on_events: Optional[OnEvents],
    ) -> None:
        mem = MemoryStreamBridge()
        try:
            async for raw in iter_bridge_events(
                mem,
                run_id,
                agent_generator,
                keepalive_seconds=0,
                langfuse_context=langfuse_context,
            ):
                if raw is HEARTBEAT_SENTINEL:
                    continue
                if raw is END_SENTINEL:
                    break
                if isinstance(raw, StreamBridgeError):
                    await self.bus.publish_error(run_id, raw.exc)
                    return
                events = mapper.map_item(raw, builder, ctx)
                if on_events is not None and events:
                    await on_events(events)
                for ev in events:
                    await self.bus.publish(run_id, ev)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.bus.publish_error(run_id, exc)
        finally:
            await self.bus.publish_end(run_id)
