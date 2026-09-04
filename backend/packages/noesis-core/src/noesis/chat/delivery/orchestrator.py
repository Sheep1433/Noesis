"""TEST_CASE_QA 旧 SSE 边界使用的轻量 RunEventBus 编排。"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional

from noesis.chat.delivery.bus import RunEventBus
from noesis.chat.delivery.events import RunEvent, RunOrigin
from noesis.chat.delivery.sse import encode_filtered, iter_sse_from_bus
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.event_mapping.bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    MemoryStreamBridge,
    StreamBridgeError,
    iter_bridge_events,
)
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge
from noesis.chat.event_mapping.mapper import RuntimeEventMapper

OnEvents = Callable[[List[RunEvent]], Awaitable[None]]


def bridge_run_id(session_id: str, assistant_message_id: str) -> str:
    return f"{session_id}:{assistant_message_id}"


class RunOrchestrator:
    """
    只为独立 TEST_CASE_QA 的旧协议提供 Fan-out 接缝。

    不依赖 noesis_runtime 搬家。raw 经 MemoryStreamBridge（无 heartbeat）→
    RuntimeEventMapper → RunEventBus；SSE 订阅并注入 keepalive。
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
        mapper = RuntimeEventMapper(bridge)
        sse_q = self.bus.subscribe_queue(run_id)

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
        mapper = RuntimeEventMapper(bridge)
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
            events = mapper.finalize(finish_reason=finish_reason)
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
        mapper = RuntimeEventMapper(bridge)
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
        mapper: RuntimeEventMapper,
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
