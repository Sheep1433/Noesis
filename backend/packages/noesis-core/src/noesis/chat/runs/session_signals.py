"""Session 级信令总线：让同一会话的其它窗口发现活跃 run。

信令定位是「提示去拉取」的 hint，不承载内容：丢一条不影响正确性——
窗口收到信令后从权威端点（active-run / getAgentRun）取状态，断线重连
时也先经 active-run 对齐。因此队列有界、满则丢弃是设计内行为。
"""

import asyncio

from noesis.runtime.logging import logger

MAX_SUBSCRIBERS_PER_SESSION = 8
_QUEUE_SIZE = 64


class SessionSignalBus:
    """(user_id, session_id) → 订阅队列集合的进程内总线。

    单实例后端（advisory lock 保证）内有效；不持久化、不跨进程。
    """

    def __init__(self) -> None:
        self._subscribers: dict[tuple[str, str], set[asyncio.Queue[dict]]] = {}

    def subscribe(self, user_id: str, session_id: str) -> asyncio.Queue[dict] | None:
        """注册一个订阅队列；超过每会话上限返回 None（调用方按 429 处理）。"""
        queues = self._subscribers.setdefault((user_id, session_id), set())
        if len(queues) >= MAX_SUBSCRIBERS_PER_SESSION:
            return None
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        queues.add(queue)
        return queue

    def unsubscribe(self, user_id: str, session_id: str, queue: asyncio.Queue[dict]) -> None:
        queues = self._subscribers.get((user_id, session_id))
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            self._subscribers.pop((user_id, session_id), None)

    def publish(self, user_id: str, session_id: str, signal: dict) -> None:
        """向本会话全部订阅者投递信令；慢订阅者丢帧，不阻塞发布方。"""
        queues = self._subscribers.get((user_id, session_id))
        if not queues:
            return
        for queue in list(queues):
            try:
                queue.put_nowait(signal)
            except asyncio.QueueFull:
                logger.warning(
                    "session signal dropped (slow subscriber) user_id={} session_id={} type={}",
                    user_id,
                    session_id,
                    signal.get("type"),
                )


session_signal_bus = SessionSignalBus()
