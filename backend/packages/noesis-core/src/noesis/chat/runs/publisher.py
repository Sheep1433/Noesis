"""每 Run 的 Run bus 发布器：单 consumer 有界 outbound queue（task 4.2）。

- 严格按 sequence 序发布：入队序即 sequence 序（delivery 内核在 lock 内
  分配后经 ``RunManager._fanout`` 入队）；终态事件经同一 queue，不得越过
  尚未发布的普通事件
- 队列满或 publish 失败：丢弃该事件并记 gap 指标（at-most-once 实时面），
  不阻塞 producer、不改变 Run 状态——远端 subscriber 经 sequence gap 检测
  与 snapshot 对账恢复
- owner term 门控：leadership token 失效（leader 失锁）后停止发布，旧 term
  事件即使发出也会被订阅端忽略，这里直接丢弃省流量
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from noesis.runtime.logging import logger
from noesis.chat.delivery.sse import sequenced_event_payloads
from noesis.chat.runs.bus import RUN_BUS_SCHEMA_VERSION, RunEventEnvelope

if TYPE_CHECKING:
    from noesis.chat.runs.manager import SequencedRunEvent


class RunEventPublisher:
    """单 Run 的 bus 发布：submit（锁内非阻塞）+ 单 consumer 协程。"""

    def __init__(
        self,
        *,
        run_id: str,
        bus: Any,
        token_provider: Callable[[], Any],
        max_events: int,
        max_bytes: int,
        on_metric: Callable[[str], None] | None = None,
    ) -> None:
        self._run_id = run_id
        self._bus = bus
        self._token_provider = token_provider
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._on_metric = on_metric
        self._queue: asyncio.Queue = asyncio.Queue()
        self._bytes = 0
        self._task: asyncio.Task | None = None
        self._closed = False

    @property
    def published(self) -> int:
        return self._published

    @property
    def dropped_overflow(self) -> int:
        return self._dropped_overflow

    @property
    def publish_failures(self) -> int:
        return self._publish_failures

    _published = 0
    _dropped_overflow = 0
    _publish_failures = 0

    def start(self) -> None:
        if self._task is None and not self._closed:
            self._task = asyncio.create_task(
                self._consume(), name=f"run-bus-publisher:{self._run_id}"
            )

    async def stop(self) -> None:
        self._closed = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def submit(self, envelope: "SequencedRunEvent") -> None:
        """锁内调用（_fanout）：非阻塞入队；满/超限丢弃并记 overflow。"""
        if self._closed or self._task is None:
            return
        item_bytes = envelope.estimated_bytes
        if (
            self._queue.qsize() >= self._max_events
            or self._bytes + item_bytes > self._max_bytes
        ):
            self._dropped_overflow += 1
            self._report("bus_publisher_overflow")
            logger.warning(
                "run bus publisher queue full, event dropped run_id={} sequence={}",
                self._run_id, envelope.sequence,
            )
            return
        self._queue.put_nowait(envelope)
        self._bytes += item_bytes

    def _report(self, name: str) -> None:
        if self._on_metric is not None:
            self._on_metric(name)

    async def _consume(self) -> None:
        while True:
            envelope = await self._queue.get()
            self._bytes = max(0, self._bytes - envelope.estimated_bytes)
            token = self._token_provider()
            if token is None or not getattr(token, "valid", False):
                # 旧 term 事件订阅端也会忽略；此处直接丢弃
                self._report("bus_publisher_stale_term")
                continue
            pairs = sequenced_event_payloads(envelope)
            if not pairs:
                continue
            bus_envelopes = [
                RunEventEnvelope(
                    schema_version=RUN_BUS_SCHEMA_VERSION,
                    run_id=self._run_id,
                    owner_instance_id=str(token.instance_id),
                    owner_term=int(token.term),
                    sequence=envelope.sequence,
                    attempt_id=envelope.attempt_id,
                    event_type=event_type,
                    payload=payload,
                )
                for event_type, payload in pairs
            ]
            try:
                await self._bus.publish_run_events(self._run_id, bus_envelopes)
                self._published += 1
                self._report("bus_publisher_events")
            except Exception:  # noqa: BLE001
                # at-most-once：单事件失败不重试，继续消费后续（gap 由
                # 订阅端 sequence 检测 + snapshot 恢复兜底）
                self._publish_failures += 1
                self._report("bus_publisher_failures")
                logger.warning(
                    "run bus publish failed run_id={} sequence={}",
                    self._run_id, envelope.sequence,
                )


__all__ = ["RunEventPublisher"]
