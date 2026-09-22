"""Redis Pub/Sub adapter：跨进程 RunEvent 广播与唤醒。

与 ``InMemoryRunBus`` 同语义（enable-distributed-sse-pubsub P4）：at-most-once、
订阅满即丢（记 dropped 计数）、不阻塞发布方。PostgreSQL 仍是恢复与终态
权威——Pub/Sub 只做在线广播，断连期间丢失的消息由上层 subscribe-first
握手、sequence gap 检测与周期 checkpoint 兜底，不在本层重放。

连接模型：一条共享 PubSub 连接承载全部 run channel 与 wakeup pattern
订阅，单一 reader 任务按 channel 分发到各订阅方的有界本地 queue（worker
内每 Run hub / 每唤醒消费者一个 queue）；发布走连接池普通连接。redis-py
断连后自动重连并重放 SUBSCRIBE/PSUBSCRIBE，重连窗口内的消息丢失由上述
上层机制兜底——reader 循环对连接异常退避重试，不向外抛。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from noesis.runtime.logging import logger
from noesis.chat.runs.bus import (
    RUN_BUS_SCHEMA_VERSION,
    SIGNAL_SCHEMA_VERSION,
    BusSubscription,
    BusWakeUp,
    EnvelopePayloadTooLarge,
    RunEventEnvelope,
    SignalSubscription,
    WakeupSubscription,
)

_SUB_QUEUE_MAX = 1024
_WAKEUP_QUEUE_MAX = 256
_RECONNECT_BACKOFF_SECONDS = 0.5


def _run_channel(cluster_id: str, run_id: str) -> str:
    return f"noesis:{cluster_id}:run:{run_id}:events"


def _wakeup_channel(cluster_id: str, topic: str) -> str:
    return f"noesis:{cluster_id}:wakeup:{topic}"


def _signal_channel(cluster_id: str, scope: str, key: str) -> str:
    return f"noesis:{cluster_id}:signal:{scope}:{key}"


def _wakeup_pattern(cluster_id: str) -> str:
    return f"noesis:{cluster_id}:wakeup:*"


class RedisRunBus:
    """RunBus 的 Redis Pub/Sub 实现（跨进程广播；恢复与终态权威在 PostgreSQL）。"""

    def __init__(self, *, client: Any, cluster_id: str, envelope_payload_max_bytes: int) -> None:
        self._client = client
        self._cluster_id = cluster_id
        self._envelope_payload_max_bytes = envelope_payload_max_bytes
        self._pubsub: Any = None
        self._reader: asyncio.Task | None = None
        # run_id -> 本地订阅 queue 集合；空集合时 UNSUBSCRIBE 底层 channel
        self._run_queues: dict[str, set[asyncio.Queue]] = {}
        # (scope, key) -> 信令订阅 queue 集合（同 run channel 生命周期）
        self._signal_queues: dict[tuple[str, str], set[asyncio.Queue]] = {}
        # channel -> ("run", run_id) | ("signal", scope, key)（reader 分发用）
        self._channels: dict[str, tuple] = {}
        self._wakeup_queues: set[asyncio.Queue] = set()
        # wakeup pattern 订阅确认 future：所有 wakeup 订阅者经 ready() 等待
        # （subscribe-first：确认后发布的唤醒保证可见）
        self._wakeup_ready: asyncio.Future | None = None
        self._wakeup_pattern_subscribed = False
        self._dropped_events = 0
        self._dropped_wakeups = 0
        self._dropped_signals = 0
        self._closed = False

    @property
    def dropped_events(self) -> int:
        return self._dropped_events

    @property
    def dropped_wakeups(self) -> int:
        return self._dropped_wakeups

    @property
    def dropped_signals(self) -> int:
        return self._dropped_signals

    async def _ensure_pubsub(self) -> None:
        """惰性建立共享 PubSub 连接与 reader（首个订阅者到达时）。"""
        if self._closed:
            raise RuntimeError("run bus closed")
        if self._pubsub is not None:
            return
        self._pubsub = self._client.pubsub()
        await self._pubsub.connect()
        self._reader = asyncio.create_task(
            self._reader_loop(), name="redis-run-bus-reader"
        )

    async def _reader_loop(self) -> None:
        pubsub = self._pubsub
        while not self._closed:
            try:
                # get_message 而非 listen()：listen 的 while subscribed 在无订阅时
                # 立即退出（外层重入即 100% CPU 空转）；get_message(timeout) 自
                # 节奏轮询，未订阅时也在超时上等待
                message = await pubsub.get_message(
                    timeout=1.0, ignore_subscribe_messages=True
                )
                if message is not None:
                    self._dispatch(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._closed:
                    return
                # 断连：redis-py 重连时自动重放订阅；窗口内消息丢失由上层
                # sequence gap / snapshot 恢复兜底（at-most-once 契约）
                logger.warning("run bus pubsub reader 断连，退避重试")
                await asyncio.sleep(_RECONNECT_BACKOFF_SECONDS)

    def _dispatch(self, message: Mapping[str, Any]) -> None:
        msg_type = message.get("type")
        if msg_type == "message":
            channel = str(message.get("channel") or "")
            target = self._channels.get(channel)
            if target is None:
                return
            if target[0] == "run":
                self._fanout_run(target[1], message.get("data"))
            else:
                self._fanout_signal(target[1], target[2], message.get("data"))
        elif msg_type == "pmessage":
            channel = str(message.get("channel") or "")
            topic = channel.rsplit(":", 1)[-1] if channel else ""
            self._fanout_wakeup(topic, message.get("data"))

    def _fanout_signal(self, scope: str, key: str, data: Any) -> None:
        try:
            message = json.loads(data)
            if not isinstance(message, dict) or int(message.get("schema_version", 0)) != SIGNAL_SCHEMA_VERSION:
                raise ValueError("invalid signal envelope")
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.warning("run bus 收到无法解析的信令，已丢弃 scope={} key={}", scope, key)
            return
        for queue in list(self._signal_queues.get((scope, key), ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._dropped_signals += 1
                logger.warning("run bus signal subscriber queue full, dropped scope={} key={}", scope, key)

    def _fanout_run(self, run_id: str, data: Any) -> None:
        try:
            envelope = RunEventEnvelope.from_dict(json.loads(data))
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.warning("run bus 收到无法解析的事件，已丢弃 run_id={}", run_id)
            return
        for queue in list(self._run_queues.get(run_id, ())):
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                self._dropped_events += 1
                logger.warning(
                    "run bus subscriber queue full, event dropped run_id={} sequence={}",
                    run_id, envelope.sequence,
                )

    def _fanout_wakeup(self, topic: str, data: Any) -> None:
        try:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError("wakeup payload must be an object")
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.warning("run bus 收到无法解析的唤醒，已丢弃 topic={}", topic)
            return
        message = BusWakeUp(topic=topic, payload=payload)
        for queue in list(self._wakeup_queues):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._dropped_wakeups += 1
                logger.warning("run bus wakeup subscriber queue full, dropped topic={}", topic)

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
        channel = _run_channel(self._cluster_id, run_id)
        for envelope in envelopes:
            await self._client.publish(channel, json.dumps(envelope.to_dict()))

    async def subscribe_run_events(self, run_id: str) -> BusSubscription:
        await self._ensure_pubsub()
        queue: asyncio.Queue = asyncio.Queue(maxsize=_SUB_QUEUE_MAX)
        channel = _run_channel(self._cluster_id, run_id)
        if channel not in self._channels:
            # SUBSCRIBE 的 Redis ack 即 ready 语义：确认后发布的事件保证可见
            await self._pubsub.subscribe(channel)
            self._channels[channel] = ("run", run_id)
        self._run_queues.setdefault(run_id, set()).add(queue)

        async def _release() -> None:
            queues = self._run_queues.get(run_id)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    self._run_queues.pop(run_id, None)
                    if not self._closed and channel in self._channels:
                        self._channels.pop(channel, None)
                        try:
                            await self._pubsub.unsubscribe(channel)
                        except Exception:  # noqa: BLE001
                            logger.warning("run bus unsubscribe 失败 channel={}", channel)

        return _RedisSubscription(queue, _release)

    async def publish_signal(
        self, scope: str, key: str, payload: Mapping[str, str], *, origin: str = ""
    ) -> None:
        if self._closed:
            return
        message = {
            "schema_version": SIGNAL_SCHEMA_VERSION,
            "scope": scope,
            "key": key,
            "origin": origin,
            "payload": dict(payload),
        }
        await self._client.publish(
            _signal_channel(self._cluster_id, scope, key), json.dumps(message)
        )

    async def subscribe_signals(self, scope: str, key: str) -> SignalSubscription:
        await self._ensure_pubsub()
        queue: asyncio.Queue = asyncio.Queue(maxsize=_SUB_QUEUE_MAX)
        channel = _signal_channel(self._cluster_id, scope, key)
        if channel not in self._channels:
            await self._pubsub.subscribe(channel)
            self._channels[channel] = ("signal", scope, key)
        self._signal_queues.setdefault((scope, key), set()).add(queue)

        async def _release() -> None:
            queues = self._signal_queues.get((scope, key))
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    self._signal_queues.pop((scope, key), None)
                    if not self._closed and channel in self._channels:
                        self._channels.pop(channel, None)
                        try:
                            await self._pubsub.unsubscribe(channel)
                        except Exception:  # noqa: BLE001
                            logger.warning("run bus signal unsubscribe 失败 channel={}", channel)

        return SignalSubscription(queue, _release)

    async def wakeup(self, topic: str, payload: Mapping[str, str]) -> None:
        if self._closed:
            return
        await self._client.publish(
            _wakeup_channel(self._cluster_id, topic), json.dumps(dict(payload))
        )

    def subscribe_wakeups(self) -> WakeupSubscription:
        if self._closed:
            raise RuntimeError("run bus closed")
        queue: asyncio.Queue = asyncio.Queue(maxsize=_WAKEUP_QUEUE_MAX)
        self._wakeup_queues.add(queue)
        # pattern 订阅是异步命令而本方法与 memory adapter 同为同步签名：
        # 订阅者经 ready() 等待确认（subscribe-first），确认前发布的唤醒不保证可见
        loop = asyncio.get_running_loop()
        if self._wakeup_ready is None:
            self._wakeup_ready = loop.create_future()
            loop.create_task(self._ensure_wakeup_pattern())

        async def _release() -> None:
            self._wakeup_queues.discard(queue)

        return _RedisWakeupSubscription(queue, _release, self._wakeup_ready)

    async def _ensure_wakeup_pattern(self) -> None:
        ready = self._wakeup_ready
        try:
            await self._ensure_pubsub()
            while not self._wakeup_pattern_subscribed and not self._closed:
                try:
                    await self._pubsub.psubscribe(_wakeup_pattern(self._cluster_id))
                    self._wakeup_pattern_subscribed = True
                except Exception:  # noqa: BLE001
                    logger.warning("run bus wakeup pattern 订阅失败，退避重试")
                    await asyncio.sleep(_RECONNECT_BACKOFF_SECONDS)
            if ready is not None and not ready.done():
                ready.set_result(None)
        except Exception as exc:  # noqa: BLE001
            if ready is not None and not ready.done():
                ready.set_exception(exc)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
        for queue in [q for queues in self._run_queues.values() for q in queues]:
            _try_put_sentinel(queue)
        for queue in [
            q for queues in self._signal_queues.values() for q in queues
        ]:
            _try_put_sentinel(queue)
        for queue in list(self._wakeup_queues):
            _try_put_sentinel(queue)
        self._run_queues.clear()
        self._channels.clear()
        self._signal_queues.clear()
        self._wakeup_queues.clear()
        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._pubsub = None
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass


def _try_put_sentinel(queue: asyncio.Queue) -> None:
    try:
        queue.put_nowait(None)
    except asyncio.QueueFull:
        pass


class _RedisSubscription(BusSubscription):
    """带引用释放的 Redis 订阅：close 后退出迭代并注销本地 queue。"""

    def __init__(self, queue: asyncio.Queue, release) -> None:
        super().__init__(queue)
        self._release = release
        self._released = False

    async def close(self) -> None:
        await super().close()
        if not self._released:
            self._released = True
            _try_put_sentinel(self._queue)
            await self._release()


class _RedisWakeupSubscription(WakeupSubscription):
    def __init__(self, queue: asyncio.Queue, release, ready: asyncio.Future) -> None:
        super().__init__(queue)
        self._release = release
        self._released = False
        self._ready = ready

    async def ready(self) -> None:
        # Redis server 的 PSUBSCRIBE 确认后才算就绪（subscribe-first）
        await self._ready

    async def close(self) -> None:
        self._closed = True
        if not self._released:
            self._released = True
            _try_put_sentinel(self._queue)
            await self._release()


def build_redis_run_bus(settings) -> RedisRunBus:
    """从 DistributedRunsSettings 构造 adapter（client 归 bus 所有，close 时释放）。"""
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        max_connections=settings.redis_pool_max_connections,
    )
    return RedisRunBus(
        client=client,
        cluster_id=settings.cluster_id,
        envelope_payload_max_bytes=settings.envelope_payload_max_bytes,
    )


__all__ = ["RedisRunBus", "build_redis_run_bus"]
