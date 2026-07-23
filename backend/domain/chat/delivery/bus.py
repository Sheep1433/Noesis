"""多订阅 RunEvent 总线（进程内）；keepalive 不得进入本总线。"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional

from domain.chat.delivery.events import RunEvent


class _EndSentinel:
    pass


class _ErrorWrapper:
    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


_END = _EndSentinel()


class RunEventBus:
    """每个 run_id 可有多个订阅者；publish 广播到全部队列。"""

    def __init__(self) -> None:
        self._subs: Dict[str, List[asyncio.Queue[Any]]] = {}

    def _queues(self, run_id: str) -> List[asyncio.Queue[Any]]:
        return self._subs.setdefault(run_id, [])

    def subscribe_queue(self, run_id: str) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue()
        self._queues(run_id).append(q)
        return q

    def unsubscribe_queue(self, run_id: str, queue: asyncio.Queue[Any]) -> None:
        qs = self._subs.get(run_id)
        if not qs:
            return
        try:
            qs.remove(queue)
        except ValueError:
            pass
        if not qs:
            self._subs.pop(run_id, None)

    async def publish(self, run_id: str, event: RunEvent) -> None:
        for q in list(self._queues(run_id)):
            await q.put(event)

    async def publish_error(self, run_id: str, exc: BaseException) -> None:
        for q in list(self._queues(run_id)):
            await q.put(_ErrorWrapper(exc))

    async def publish_end(self, run_id: str) -> None:
        for q in list(self._queues(run_id)):
            await q.put(_END)

    async def iter_events(
        self,
        run_id: str,
        *,
        queue: Optional[asyncio.Queue[Any]] = None,
    ) -> AsyncGenerator[RunEvent, None]:
        """订阅直至 end / error。不注入 heartbeat。"""
        own = queue is None
        q = queue or self.subscribe_queue(run_id)
        try:
            while True:
                item = await q.get()
                if item is _END:
                    return
                if isinstance(item, _ErrorWrapper):
                    raise item.exc
                yield item  # type: ignore[misc]
        finally:
            if own:
                self.unsubscribe_queue(run_id, q)

    def cleanup(self, run_id: str) -> None:
        self._subs.pop(run_id, None)


def is_bus_end(item: Any) -> bool:
    return item is _END or isinstance(item, _EndSentinel)


def is_bus_error(item: Any) -> bool:
    return isinstance(item, _ErrorWrapper)


def bus_error_exc(item: Any) -> BaseException:
    assert isinstance(item, _ErrorWrapper)
    return item.exc
