"""进程内流式事件中介：解耦 Agent 生产者与 SSE 消费者。"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, Optional


class _HeartbeatSentinel:
    """订阅空闲超时占位，由 SSE 消费者转为注释保活帧。"""


class _EndSentinel:
    """生产者正常结束占位。"""


HEARTBEAT_SENTINEL = _HeartbeatSentinel()
END_SENTINEL = _EndSentinel()


class StreamBridgeError:
    """生产者 Task 未处理异常，经中介传递给 SSE 消费者。"""

    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


_INTERNAL_END = object()


class MemoryStreamBridge:
    """单次 HTTP 连接使用的进程内 Queue 桥接层。"""

    def __init__(self) -> None:
        self._queues: Dict[str, asyncio.Queue[Any]] = {}

    def _queue(self, run_id: str) -> asyncio.Queue[Any]:
        if run_id not in self._queues:
            self._queues[run_id] = asyncio.Queue()
        return self._queues[run_id]

    async def publish(self, run_id: str, item: Any) -> None:
        await self._queue(run_id).put(item)

    async def publish_error(self, run_id: str, exc: BaseException) -> None:
        await self._queue(run_id).put(StreamBridgeError(exc))

    async def publish_end(self, run_id: str) -> None:
        await self._queue(run_id).put(_INTERNAL_END)

    async def subscribe(
        self,
        run_id: str,
        heartbeat_interval: float,
    ) -> AsyncGenerator[Any, None]:
        """
        订阅业务事件。``heartbeat_interval > 0`` 时空闲超时产出 ``HEARTBEAT_SENTINEL``；
        收到结束哨兵产出 ``END_SENTINEL``；收到异常包装后产出 ``StreamBridgeError`` 并结束。
        """
        queue = self._queue(run_id)
        if heartbeat_interval <= 0:
            while True:
                item = await queue.get()
                if item is _INTERNAL_END:
                    yield END_SENTINEL
                    return
                if isinstance(item, StreamBridgeError):
                    yield item
                    return
                yield item
            return

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                yield HEARTBEAT_SENTINEL
                continue
            if item is _INTERNAL_END:
                yield END_SENTINEL
                return
            if isinstance(item, StreamBridgeError):
                yield item
                return
            yield item

    def cleanup(self, run_id: str) -> None:
        self._queues.pop(run_id, None)


async def run_stream_producer(
    agent_generator: AsyncGenerator[Any, None],
    bridge: MemoryStreamBridge,
    run_id: str,
    *,
    langfuse_context: Optional[Any] = None,
) -> None:
    """
    在独立 Task 内顺序消费上游 generator 并发布到桥接层。
    ``langfuse_context`` 为 ``langfuse_workflow_context`` 返回的 context manager（或 None）。
    """
    try:
        if langfuse_context is not None:
            with langfuse_context:
                async for raw in agent_generator:
                    await bridge.publish(run_id, raw)
        else:
            async for raw in agent_generator:
                await bridge.publish(run_id, raw)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await bridge.publish_error(run_id, exc)
    finally:
        await bridge.publish_end(run_id)


async def iter_bridge_events(
    bridge: MemoryStreamBridge,
    run_id: str,
    agent_generator: AsyncGenerator[Any, None],
    *,
    keepalive_seconds: float,
    langfuse_context: Optional[Any] = None,
) -> AsyncGenerator[Any, None]:
    """启动生产者 Task 并订阅桥接层事件；退出时取消生产者并清理 run_id。"""
    producer = asyncio.create_task(
        run_stream_producer(
            agent_generator,
            bridge,
            run_id,
            langfuse_context=langfuse_context,
        )
    )
    try:
        async for event in bridge.subscribe(run_id, keepalive_seconds):
            yield event
            if event is END_SENTINEL or isinstance(event, StreamBridgeError):
                break
    finally:
        if not producer.done():
            producer.cancel()
            try:
                await producer
            except (asyncio.CancelledError, Exception):
                pass
        bridge.cleanup(run_id)
