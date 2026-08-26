"""用户级信令总线：会话列表实时感知任意会话的 run 状态变化。

与 ``session_signals`` 同一哲学：信令是「提示去拉取」的 hint，不承载
内容。丢一条不影响正确性——前端收到后 patch 列表行（行不在则全量
刷新）；断线重连时端点首帧下发用户全部活跃 run 定位符对齐。队列有界、
满则丢弃是设计内行为。

与 ``enable-distributed-sse-pubsub`` 规划的关系：本总线是进程内实现；
Redis Pub/Sub fan-out 落地时用户级信令随会话级信令走同一条广播链。
"""

from __future__ import annotations

import asyncio

from noesis.runtime.logging import logger

MAX_SUBSCRIBERS_PER_USER = 16
_QUEUE_SIZE = 64


class UserSignalBus:
    """user_id → 订阅队列集合的进程内总线。

    单实例后端（advisory lock 保证）内有效；不持久化、不跨进程。
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict]]] = {}

    def subscribe(self, user_id: str) -> asyncio.Queue[dict] | None:
        """注册一个订阅队列；超过每用户上限返回 None（调用方按 429 处理）。"""
        queues = self._subscribers.setdefault(user_id, set())
        if len(queues) >= MAX_SUBSCRIBERS_PER_USER:
            return None
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        queues.add(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue[dict]) -> None:
        queues = self._subscribers.get(user_id)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            self._subscribers.pop(user_id, None)

    def publish(self, user_id: str, signal: dict) -> None:
        """向该用户全部订阅者投递信令；慢订阅者丢帧，不阻塞发布方。"""
        queues = self._subscribers.get(user_id)
        if not queues:
            return
        for queue in list(queues):
            try:
                queue.put_nowait(signal)
            except asyncio.QueueFull:
                logger.warning(
                    "user signal dropped (slow subscriber) user_id={} type={}",
                    user_id,
                    signal.get("type"),
                )


user_signal_bus = UserSignalBus()
