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
        # 跨进程桥（redis 模式装配）：本地 fan-out 不变，发布同时上 bus，
        # 远端信令泵回本地（回声抑制）。memory 模式为 None，行为不变
        self._bridge = None

    def attach_bridge(self, bridge) -> None:
        self._bridge = bridge

    def subscribe(self, user_id: str, session_id: str) -> asyncio.Queue[dict] | None:
        """注册一个订阅队列；超过每会话上限返回 None（调用方按 429 处理）。"""
        queues = self._subscribers.setdefault((user_id, session_id), set())
        if len(queues) >= MAX_SUBSCRIBERS_PER_SESSION:
            return None
        if not queues and self._bridge is not None:
            self._bridge.ensure_pump(
                "session", session_id,
                lambda payload: self._publish_local_by_session(session_id, payload),
            )
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
            if self._bridge is not None and not self._session_subscribed(session_id):
                self._bridge.release_pump("session", session_id)

    def _session_subscribed(self, session_id: str) -> bool:
        return any(sid == session_id for _, sid in self._subscribers)

    def _publish_local_by_session(self, session_id: str, signal: dict) -> None:
        """远端泵投回：按 session_id 找本地订阅（会话唯一属于一个 user）。"""
        for (_, sid), queues in self._subscribers.items():
            if sid == session_id:
                self._fanout(queues, signal, sid)

    def publish(self, user_id: str, session_id: str, signal: dict) -> None:
        """本地 fan-out + 跨进程广播（桥装配时）。"""
        queues = self._subscribers.get((user_id, session_id))
        if queues:
            self._fanout(queues, signal, session_id)
        if self._bridge is not None:
            self._bridge.publish("session", session_id, signal)

    def _fanout(self, queues, signal: dict, session_id: str) -> None:
        """向订阅者投递信令；慢订阅者丢帧，不阻塞发布方。"""
        for queue in list(queues):
            try:
                queue.put_nowait(signal)
            except asyncio.QueueFull:
                logger.warning(
                    "session signal dropped (slow subscriber) session_id={} type={}",
                    session_id,
                    signal.get("type"),
                )


session_signal_bus = SessionSignalBus()
