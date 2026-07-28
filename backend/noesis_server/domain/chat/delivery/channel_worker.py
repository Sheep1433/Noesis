"""非 durable 的 ChannelDelivery worker：有界队列、失败隔离、进程内 drain。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any


FailureHandler = Callable[[str], Awaitable[None]]


class ChannelDeliveryWorker:
    def __init__(
        self,
        outbound: Any,
        *,
        max_batches: int,
        max_bytes: int,
        drain_seconds: float,
        on_failure: FailureHandler,
    ) -> None:
        self.outbound = outbound
        self.max_bytes = max_bytes
        self.drain_seconds = drain_seconds
        self.on_failure = on_failure
        self.queue: asyncio.Queue[list[Any] | None] = asyncio.Queue(maxsize=max_batches)
        self.queued_bytes = 0
        self.failed = False
        self.closed = False
        self.task = asyncio.create_task(self._run(), name="channel-delivery")

    @staticmethod
    def _estimate(events: list[Any]) -> int:
        return len(json.dumps(events, default=str, ensure_ascii=False).encode("utf-8")) + 64

    async def submit(self, events: list[Any]) -> bool:
        if self.closed or self.failed or not events:
            return False
        size = self._estimate(events)
        if self.queued_bytes + size > self.max_bytes or self.queue.full():
            self.failed = True
            await self.on_failure("CHANNEL_QUEUE_OVERFLOW")
            return False
        self.queued_bytes += size
        self.queue.put_nowait(events)
        return True

    async def _run(self) -> None:
        while True:
            events = await self.queue.get()
            if events is None:
                return
            self.queued_bytes = max(0, self.queued_bytes - self._estimate(events))
            if self.failed:
                continue
            try:
                await self.outbound.feed_events(events)
            except Exception:
                self.failed = True
                await self.on_failure("CHANNEL_SEND_FAILED")

    async def finalize(self) -> bool:
        if self.closed:
            return not self.failed
        self.closed = True
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            self.failed = True
            await self.on_failure("CHANNEL_QUEUE_OVERFLOW")
            self.task.cancel()
        try:
            await asyncio.wait_for(self.task, timeout=self.drain_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.failed = True
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            await self.on_failure("CHANNEL_DRAIN_TIMEOUT")
        if self.failed:
            return False
        try:
            await self.outbound.finalize()
            return True
        except Exception:
            self.failed = True
            await self.on_failure("CHANNEL_FINALIZE_FAILED")
            return False
