"""Run bus：Run 事件的实时广播 port 与进程内 memory adapter。

enable-distributed-sse-pubsub 决策 3：bus 只做「在线广播」，PostgreSQL
仍是恢复与终态权威。Pub/Sub 语义为 at-most-once——丢失由 subscribe-first
握手、sequence gap 检测与周期 checkpoint 兜底（P4 remote hub）。

Service 层只依赖 ``RunBus`` port；memory/redis adapter 共享同一版本化
envelope 与同一状态机，仅 transport 不同。禁止按 backend 分叉业务实现。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from noesis.runtime.logging import logger

RUN_BUS_SCHEMA_VERSION = 1

# wakeup topic 约定：dispatcher 消费 run-created；P2 command consumer 消费 run-command
WAKEUP_TOPIC_RUN_CREATED = "run-created"
WAKEUP_TOPIC_RUN_COMMAND = "run-command"


class EnvelopePayloadTooLarge(RuntimeError):
    """envelope payload 超过配置上限，拒发（防止压垮订阅端握手 buffer）。"""


@dataclass(frozen=True)
class RunEventEnvelope:
    """跨进程 Run 事件的版本化信封。

    payload 为 JSON 兼容 dict；认证秘密禁止进入 envelope。
    """

    schema_version: int
    run_id: str
    owner_instance_id: str
    owner_term: int
    sequence: int
    attempt_id: int
    event_type: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "owner_instance_id": self.owner_instance_id,
            "owner_term": self.owner_term,
            "sequence": self.sequence,
            "attempt_id": self.attempt_id,
            "event_type": self.event_type,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunEventEnvelope:
        version = int(data.get("schema_version", 0))
        if version != RUN_BUS_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported run bus envelope schema_version={version}"
            )
        return cls(
            schema_version=version,
            run_id=str(data["run_id"]),
            owner_instance_id=str(data["owner_instance_id"]),
            owner_term=int(data["owner_term"]),
            sequence=int(data["sequence"]),
            attempt_id=int(data["attempt_id"]),
            event_type=str(data["event_type"]),
            payload=dict(data.get("payload") or {}),
        )

    def payload_bytes(self) -> int:
        return len(json.dumps(self.payload, ensure_ascii=False, default=str))


@dataclass(frozen=True)
class BusWakeUp:
    """轻量唤醒信号（run-created / run-command），只带标识不带事件内容。"""

    topic: str
    payload: Mapping[str, str]


class BusSubscription:
    """单个订阅端句柄：ready() 为 adapter ack，此后发布的事件保证可见。"""

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue
        self._closed = False

    async def ready(self) -> None:
        # memory adapter 注册即生效；redis adapter（P4）在此等待订阅确认
        return None

    def __aiter__(self) -> AsyncIterator[RunEventEnvelope]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[RunEventEnvelope]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self._closed = True


@runtime_checkable
class RunBus(Protocol):
    """Run 事件广播 port。Service 层禁止依赖具体 adapter 类型。"""

    async def publish_run_events(
        self, run_id: str, envelopes: Sequence[RunEventEnvelope]
    ) -> None: ...

    async def subscribe_run_events(self, run_id: str) -> BusSubscription: ...

    async def wakeup(self, topic: str, payload: Mapping[str, str]) -> None: ...

    def subscribe_wakeups(self) -> "WakeupSubscription": ...

    async def close(self) -> None: ...


class WakeupSubscription:
    """wakeup 广播订阅：ready() 后发布的唤醒保证可见（subscribe-first）。"""

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue

    async def ready(self) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[BusWakeUp]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[BusWakeUp]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self._closed = True


class InMemoryRunBus:
    """进程内 adapter：单进程部署（memory 模式）与契约测试共用。

    与 redis adapter 语义一致：at-most-once、订阅满即丢（记录 gap 计数），
    不阻塞发布方。
    """

    def __init__(self, *, envelope_payload_max_bytes: int) -> None:
        self._envelope_payload_max_bytes = envelope_payload_max_bytes
        self._run_channels: dict[str, set[asyncio.Queue]] = {}
        self._wakeup_subscribers: set[asyncio.Queue] = set()
        self._dropped_events = 0
        self._dropped_wakeups = 0
        self._closed = False

    @property
    def dropped_events(self) -> int:
        return self._dropped_events

    @property
    def dropped_wakeups(self) -> int:
        return self._dropped_wakeups

    async def publish_run_events(
        self, run_id: str, envelopes: Sequence[RunEventEnvelope]
    ) -> None:
        if self._closed:
            return
        for envelope in envelopes:
            if envelope.schema_version != RUN_BUS_SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported run bus envelope schema_version={envelope.schema_version}"
                )
            if envelope.payload_bytes() > self._envelope_payload_max_bytes:
                raise EnvelopePayloadTooLarge(
                    f"run bus envelope payload exceeds limit run_id={run_id} "
                    f"event_type={envelope.event_type}"
                )
        subscribers = list(self._run_channels.get(run_id, ()))
        for envelope in envelopes:
            for queue in subscribers:
                try:
                    queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    self._dropped_events += 1
                    logger.warning(
                        "run bus subscriber queue full, event dropped run_id={} sequence={}",
                        run_id,
                        envelope.sequence,
                    )

    async def subscribe_run_events(self, run_id: str) -> BusSubscription:
        if self._closed:
            raise RuntimeError("run bus closed")
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        channel = self._run_channels.setdefault(run_id, set())
        channel.add(queue)

        async def _release() -> None:
            channel.discard(queue)
            if not channel:
                self._run_channels.pop(run_id, None)

        subscription = _MemoryBusSubscription(queue, _release)
        return subscription

    async def wakeup(self, topic: str, payload: Mapping[str, str]) -> None:
        if self._closed:
            return
        message = BusWakeUp(topic=topic, payload=dict(payload))
        for queue in list(self._wakeup_subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._dropped_wakeups += 1
                logger.warning(
                    "run bus wakeup subscriber queue full, wakeup dropped topic={}",
                    topic,
                )

    def subscribe_wakeups(self) -> WakeupSubscription:
        if self._closed:
            raise RuntimeError("run bus closed")
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._wakeup_subscribers.add(queue)

        async def _release() -> None:
            self._wakeup_subscribers.discard(queue)

        return _MemoryWakeupSubscription(queue, _release)

    async def close(self) -> None:
        self._closed = True
        for channel in list(self._run_channels.values()):
            for queue in list(channel):
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
        self._run_channels.clear()
        for queue in list(self._wakeup_subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._wakeup_subscribers.clear()


class _MemoryBusSubscription(BusSubscription):
    """带引用释放的进程内订阅：close 后退出迭代并从 channel 注销。"""

    def __init__(self, queue: asyncio.Queue, release) -> None:
        super().__init__(queue)
        self._release = release
        self._released = False

    async def close(self) -> None:
        await super().close()
        if not self._released:
            self._released = True
            try:
                self._queue.put_nowait(None)  # 终止迭代
            except asyncio.QueueFull:
                pass
            await self._release()


class _MemoryWakeupSubscription(WakeupSubscription):
    def __init__(self, queue: asyncio.Queue, release) -> None:
        super().__init__(queue)
        self._release = release
        self._released = False

    async def close(self) -> None:
        self._closed = True
        if not self._released:
            self._released = True
            try:
                self._queue.put_nowait(None)  # 终止迭代
            except asyncio.QueueFull:
                pass
            await self._release()


__all__ = [
    "RUN_BUS_SCHEMA_VERSION",
    "WAKEUP_TOPIC_RUN_CREATED",
    "WAKEUP_TOPIC_RUN_COMMAND",
    "BusSubscription",
    "BusWakeUp",
    "EnvelopePayloadTooLarge",
    "InMemoryRunBus",
    "RunBus",
    "RunEventEnvelope",
    "WakeupSubscription",
]
