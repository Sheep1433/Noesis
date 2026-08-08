"""进程内 RunManager：producer 与 Delivery 生命周期分离。"""

from __future__ import annotations

import asyncio
import copy
import json
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from noesis.runtime.logging import logger
from noesis.domain.chat.runs.models import (
    RunSnapshot,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    require_transition,
)


class RunNotFound(KeyError):
    pass


class SlowSubscriber(RuntimeError):
    pass


class RunCapacityExceeded(RuntimeError):
    pass


class RunLimitExceeded(RuntimeError):
    error_code = "LIMIT_EXCEEDED"


class RunDurationExceeded(RunLimitExceeded):
    error_code = "RUN_TIMEOUT"


class RunOutputExceeded(RunLimitExceeded):
    error_code = "LIMIT_EXCEEDED"


class HitlPendingExpired(RunLimitExceeded):
    error_code = "HITL_TIMEOUT"


class StaleAttemptEvent(RuntimeError):
    pass


class BoundedEventQueue(asyncio.Queue):
    """同时限制事件数和估算字节数的 subscriber queue。"""

    def __init__(self, *, maxsize: int, max_bytes: int) -> None:
        super().__init__(maxsize=maxsize)
        self.max_bytes = max_bytes
        self.current_bytes = 0

    @staticmethod
    def _item_bytes(item: Any) -> int:
        if isinstance(item, SequencedRunEvent):
            return item.estimated_bytes
        return len(repr(item).encode("utf-8")) + 32

    def put_nowait(self, item: Any) -> None:
        item_bytes = self._item_bytes(item)
        if self.current_bytes + item_bytes > self.max_bytes:
            raise asyncio.QueueFull
        super().put_nowait(item)
        self.current_bytes += item_bytes

    async def get(self) -> Any:
        item = await super().get()
        self.current_bytes = max(0, self.current_bytes - self._item_bytes(item))
        return item

    def get_nowait(self) -> Any:
        item = super().get_nowait()
        self.current_bytes = max(0, self.current_bytes - self._item_bytes(item))
        return item


@dataclass(frozen=True)
class SequencedRunEvent:
    run_id: str
    sequence: int
    attempt_id: int
    event: Any

    @property
    def estimated_bytes(self) -> int:
        try:
            return len(json.dumps(self.event, default=str, ensure_ascii=False).encode("utf-8")) + 64
        except (TypeError, ValueError):
            return len(repr(self.event).encode("utf-8")) + 64


@dataclass(frozen=True)
class RunSubscription:
    snapshot: RunSnapshot
    queue: asyncio.Queue[SequencedRunEvent | SlowSubscriber]
    replay: tuple[SequencedRunEvent, ...] = ()


Producer = Callable[[Callable[[Any], Awaitable[SequencedRunEvent]]], Awaitable[None]]
SnapshotProvider = Callable[[int, RunStatus, int], RunSnapshot]
LimitHandler = Callable[[RunLimitExceeded], Awaitable[None]]
ResumePrepare = Callable[[], None]
DeliveryHandler = Callable[[SequencedRunEvent], Awaitable[None]]


@dataclass
class RunHandle:
    run_id: str
    session_id: str
    user_id: str
    assistant_message_id: str
    status: RunStatus
    attempt_id: int
    snapshot_provider: SnapshotProvider
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    producer_task: Optional[asyncio.Task[None]] = None
    next_sequence: int = 1
    buffer: deque[SequencedRunEvent] = field(default_factory=deque)
    buffer_bytes: int = 0
    subscribers: set[asyncio.Queue[SequencedRunEvent | SlowSubscriber]] = field(default_factory=set)
    delivery_queues: dict[str, asyncio.Queue[SequencedRunEvent | SlowSubscriber]] = field(
        default_factory=dict
    )
    delivery_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    delivery_failures: dict[str, BaseException] = field(default_factory=dict)
    terminal_future: asyncio.Future[RunStatus] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    watchdog_task: asyncio.Task[None] | None = None
    hitl_timeout_task: asyncio.Task[None] | None = None
    output_bytes: int = 0
    limit_error: RunLimitExceeded | None = None
    limit_handler: LimitHandler | None = None
    state: Any = None

    @property
    def last_sequence(self) -> int:
        return self.next_sequence - 1


class RunManager:
    def __init__(
        self,
        *,
        max_buffer_events: int = 2_000,
        max_buffer_bytes: int = 4 * 1024 * 1024,
        subscriber_queue_events: int = 512,
        subscriber_queue_bytes: int = 1024 * 1024,
        max_active_runs: int = 100,
        max_user_active_runs: int = 4,
        terminal_retention_seconds: float = 300.0,
        max_run_duration_seconds: float = 900.0,
        max_output_bytes: int = 16 * 1024 * 1024,
        hitl_pending_timeout_seconds: float = 86400.0,
        cancel_grace_seconds: float = 2.0,
    ) -> None:
        if min(
            max_buffer_events,
            max_buffer_bytes,
            subscriber_queue_events,
            subscriber_queue_bytes,
            max_run_duration_seconds,
            max_output_bytes,
            hitl_pending_timeout_seconds,
        ) <= 0:
            raise ValueError("run manager limits must be positive")
        if cancel_grace_seconds < 0:
            raise ValueError("cancel grace must not be negative")
        self._runs: dict[str, RunHandle] = {}
        self._registry_lock = asyncio.Lock()
        self.max_buffer_events = max_buffer_events
        self.max_buffer_bytes = max_buffer_bytes
        self.subscriber_queue_events = subscriber_queue_events
        self.subscriber_queue_bytes = subscriber_queue_bytes
        self.max_active_runs = max_active_runs
        self.max_user_active_runs = max_user_active_runs
        self.terminal_retention_seconds = terminal_retention_seconds
        self.max_run_duration_seconds = max_run_duration_seconds
        self.max_output_bytes = max_output_bytes
        self.hitl_pending_timeout_seconds = hitl_pending_timeout_seconds
        self.cancel_grace_seconds = cancel_grace_seconds
        self._metrics: dict[str, int | float] = {
            "published_events": 0,
            "published_bytes": 0,
            "subscriber_overflow": 0,
            "checkpoint_failures": 0,
            "reconnect_subscriptions": 0,
            "cancel_count": 0,
            "cancel_latency_last_ms": 0.0,
            "terminal_reclaimed": 0,
            "stale_attempt_events": 0,
            "delivery_failures": 0,
        }

    async def start(
        self,
        *,
        run_id: str,
        session_id: str,
        user_id: str,
        assistant_message_id: str,
        snapshot_provider: SnapshotProvider,
        producer: Producer,
        attempt_id: int = 1,
        state: Any = None,
        limit_handler: LimitHandler | None = None,
        deliveries: dict[str, DeliveryHandler] | None = None,
    ) -> RunHandle:
        loop = asyncio.get_running_loop()
        handle = RunHandle(
            run_id=run_id,
            session_id=session_id,
            user_id=user_id,
            assistant_message_id=assistant_message_id,
            status=RunStatus.QUEUED,
            attempt_id=attempt_id,
            snapshot_provider=snapshot_provider,
            terminal_future=loop.create_future(),
            state=state,
            limit_handler=limit_handler,
        )
        async with self._registry_lock:
            if run_id in self._runs:
                raise ValueError(f"run already registered: {run_id}")
            active = [run for run in self._runs.values() if run.status not in TERMINAL_RUN_STATUSES]
            if len(active) >= self.max_active_runs:
                raise RunCapacityExceeded("active run limit exceeded")
            if sum(1 for run in active if run.user_id == user_id) >= self.max_user_active_runs:
                raise RunCapacityExceeded("user active run limit exceeded")
            self._runs[run_id] = handle
        for name, handler in (deliveries or {}).items():
            await self.register_delivery(run_id, name, handler)
        logger.info(
            "agent_run_registered run_id={} session_id={} assistant_message_id={} attempt_id={} status=running",
            run_id,
            session_id,
            assistant_message_id,
            attempt_id,
        )
        handle.status = RunStatus.RUNNING
        handle.producer_task = asyncio.create_task(
            self._run_producer(handle, producer), name=f"agent-run:{run_id}"
        )
        handle.watchdog_task = asyncio.create_task(
            self._expire_running_run(handle), name=f"agent-run-timeout:{run_id}"
        )
        return handle

    async def _expire_running_run(self, handle: RunHandle) -> None:
        await asyncio.sleep(self.max_run_duration_seconds)
        if handle.status in TERMINAL_RUN_STATUSES:
            return
        handle.limit_error = RunDurationExceeded("run duration limit exceeded")
        if handle.limit_handler is not None:
            await handle.limit_handler(handle.limit_error)
        task = handle.producer_task
        if task is not None and not task.done():
            task.cancel()

    async def _expire_hitl_pending(self, handle: RunHandle) -> None:
        await asyncio.sleep(self.hitl_pending_timeout_seconds)
        if handle.status != RunStatus.HITL_PENDING:
            return
        handle.limit_error = HitlPendingExpired("HITL pending timeout")
        if handle.limit_handler is not None:
            await handle.limit_handler(handle.limit_error)
        task = handle.producer_task
        if task is not None and not task.done():
            task.cancel()
        elif handle.status not in TERMINAL_RUN_STATUSES:
            await self.transition(handle.run_id, RunStatus.ERROR)

    async def resume(
        self,
        run_id: str,
        producer: Producer,
        *,
        prepare: ResumePrepare | None = None,
    ) -> RunHandle:
        handle = self.get(run_id)
        async with handle.lock:
            if handle.status != RunStatus.HITL_PENDING:
                raise ValueError(f"run is not hitl_pending: {run_id}")
            if handle.producer_task is not None and not handle.producer_task.done():
                raise ValueError(f"run producer is still active: {run_id}")
            if handle.hitl_timeout_task is not None:
                handle.hitl_timeout_task.cancel()
                handle.hitl_timeout_task = None
            if prepare is not None:
                prepare()
            handle.limit_error = None
            handle.status = RunStatus.RUNNING
            handle.producer_task = asyncio.create_task(
                self._run_producer(handle, producer), name=f"agent-run-resume:{run_id}"
            )
            return handle

    async def _run_producer(self, handle: RunHandle, producer: Producer) -> None:
        try:
            await producer(
                lambda event, attempt_id=None: self.publish_attempt(
                    handle.run_id, event, attempt_id
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.transition(handle.run_id, RunStatus.ERROR)
            raise

    def get(self, run_id: str) -> RunHandle:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFound(run_id) from exc

    async def register_delivery(
        self, run_id: str, name: str, handler: DeliveryHandler
    ) -> asyncio.Task[None]:
        """注册独立 Delivery worker；handler 失败只移除自身订阅。"""
        handle = self.get(run_id)
        async with handle.lock:
            if name in handle.delivery_tasks:
                raise ValueError(f"delivery already registered: {name}")
            queue: asyncio.Queue[SequencedRunEvent | SlowSubscriber] = BoundedEventQueue(
                maxsize=self.subscriber_queue_events,
                max_bytes=self.subscriber_queue_bytes,
            )
            handle.subscribers.add(queue)
            handle.delivery_queues[name] = queue
            task = asyncio.create_task(
                self._run_delivery(handle, name, queue, handler),
                name=f"agent-run-delivery:{name}:{run_id}",
            )
            handle.delivery_tasks[name] = task
            return task

    async def _run_delivery(
        self,
        handle: RunHandle,
        name: str,
        queue: asyncio.Queue[SequencedRunEvent | SlowSubscriber],
        handler: DeliveryHandler,
    ) -> None:
        try:
            while True:
                item = await queue.get()
                try:
                    if isinstance(item, SlowSubscriber):
                        raise item
                    await handler(item)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            handle.delivery_failures[name] = exc
            self._metrics["delivery_failures"] += 1
            logger.exception(
                "agent_run_delivery_failed run_id={} delivery={}", handle.run_id, name
            )
        finally:
            async with handle.lock:
                handle.subscribers.discard(queue)
                handle.delivery_queues.pop(name, None)
                if handle.delivery_tasks.get(name) is asyncio.current_task():
                    handle.delivery_tasks.pop(name, None)

    async def drain_delivery(self, run_id: str, name: str) -> None:
        """等待指定 Delivery 消费完已入队事件，不传播其 handler 异常。"""
        handle = self.get(run_id)
        queue = getattr(handle, "delivery_queues", {}).get(name)
        if queue is not None:
            await queue.join()

    async def unregister_delivery(self, run_id: str, name: str) -> None:
        handle = self.get(run_id)
        async with handle.lock:
            queue = handle.delivery_queues.pop(name, None)
            task = handle.delivery_tasks.pop(name, None)
            if queue is not None:
                handle.subscribers.discard(queue)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def transition(self, run_id: str, target: RunStatus) -> bool:
        handle = self.get(run_id)
        async with handle.lock:
            current = handle.status
            require_transition(current, target)
            if current == target:
                return False
            handle.status = target
            if target == RunStatus.HITL_PENDING:
                if handle.hitl_timeout_task is not None:
                    handle.hitl_timeout_task.cancel()
                handle.hitl_timeout_task = asyncio.create_task(
                    self._expire_hitl_pending(handle), name=f"agent-run-hitl-timeout:{run_id}"
                )
            if target in TERMINAL_RUN_STATUSES and handle.terminal_future is not None:
                for task in (handle.watchdog_task, handle.hitl_timeout_task):
                    if task is not None and task is not asyncio.current_task():
                        task.cancel()
                handle.watchdog_task = None
                handle.hitl_timeout_task = None
                if not handle.terminal_future.done():
                    handle.terminal_future.set_result(target)
                if handle.cleanup_task is None:
                    handle.cleanup_task = asyncio.create_task(self._cleanup_after_retention(run_id))
            return True

    async def _cleanup_after_retention(self, run_id: str) -> None:
        if self.terminal_retention_seconds > 0:
            await asyncio.sleep(self.terminal_retention_seconds)
        try:
            await self.remove_terminal(run_id)
        except RunNotFound:
            pass

    async def publish(self, run_id: str, event: Any) -> SequencedRunEvent:
        return await self.publish_attempt(run_id, event)

    async def publish_attempt(
        self, run_id: str, event: Any, attempt_id: int | None = None
    ) -> SequencedRunEvent:
        handle = self.get(run_id)
        async with handle.lock:
            if attempt_id is not None and attempt_id != handle.attempt_id:
                self._metrics["stale_attempt_events"] += 1
                logger.warning(
                    "agent_run_stale_attempt run_id={} event_attempt={} current_attempt={}",
                    run_id,
                    attempt_id,
                    handle.attempt_id,
                )
                raise StaleAttemptEvent(
                    f"stale attempt event: run={run_id} event={attempt_id} current={handle.attempt_id}"
                )
            envelope = SequencedRunEvent(
                run_id=run_id,
                sequence=handle.next_sequence,
                attempt_id=handle.attempt_id,
                event=event,
            )
            if handle.output_bytes + envelope.estimated_bytes > self.max_output_bytes:
                handle.limit_error = RunOutputExceeded("run output limit exceeded")
                raise handle.limit_error
            handle.output_bytes += envelope.estimated_bytes
            self._metrics["published_events"] += 1
            self._metrics["published_bytes"] += envelope.estimated_bytes
            handle.next_sequence += 1
            handle.buffer.append(envelope)
            handle.buffer_bytes += envelope.estimated_bytes
            self._trim_buffer(handle)
            overflowed: list[asyncio.Queue[SequencedRunEvent | SlowSubscriber]] = []
            for queue in tuple(handle.subscribers):
                try:
                    queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    overflowed.append(queue)
            for queue in overflowed:
                self._metrics["subscriber_overflow"] += 1
                logger.warning(
                    "agent_run_subscriber_overflow run_id={} sequence={} queue_events={} queue_bytes={}",
                    run_id,
                    envelope.sequence,
                    queue.qsize(),
                    getattr(queue, "current_bytes", 0),
                )
                handle.subscribers.discard(queue)
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(SlowSubscriber("subscriber queue overflow"))
                except asyncio.QueueFull:
                    pass
            return envelope

    async def advance_attempt(self, run_id: str, attempt_id: int) -> None:
        handle = self.get(run_id)
        async with handle.lock:
            if attempt_id <= handle.attempt_id:
                raise ValueError("attempt_id must increase")
            handle.attempt_id = attempt_id

    def _trim_buffer(self, handle: RunHandle) -> None:
        while handle.buffer and (
            len(handle.buffer) > self.max_buffer_events
            or handle.buffer_bytes > self.max_buffer_bytes
        ):
            removed = handle.buffer.popleft()
            handle.buffer_bytes -= removed.estimated_bytes

    async def subscribe(self, run_id: str, *, after_sequence: int = 0) -> RunSubscription:
        handle = self.get(run_id)
        async with handle.lock:
            if after_sequence > 0:
                self._metrics["reconnect_subscriptions"] += 1
            queue: asyncio.Queue[SequencedRunEvent | SlowSubscriber] = BoundedEventQueue(
                maxsize=self.subscriber_queue_events,
                max_bytes=self.subscriber_queue_bytes,
            )
            handle.subscribers.add(queue)
            snapshot = copy.deepcopy(
                handle.snapshot_provider(handle.last_sequence, handle.status, handle.attempt_id)
            )
            buffered = tuple(event for event in handle.buffer if event.sequence > after_sequence)
            continuous = not buffered or buffered[0].sequence == after_sequence + 1
            replay = buffered if after_sequence > 0 and continuous else ()
            return RunSubscription(snapshot=snapshot, queue=queue, replay=replay)

    async def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[SequencedRunEvent | SlowSubscriber]
    ) -> None:
        handle = self.get(run_id)
        async with handle.lock:
            handle.subscribers.discard(queue)

    async def stop(self, run_id: str) -> bool:
        handle = self.get(run_id)
        if handle.status in TERMINAL_RUN_STATUSES:
            return False
        started = asyncio.get_running_loop().time()
        await self.transition(run_id, RunStatus.PARTIAL)
        task = handle.producer_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=self.cancel_grace_seconds)
        self._metrics["cancel_count"] += 1
        self._metrics["cancel_latency_last_ms"] = (
            asyncio.get_running_loop().time() - started
        ) * 1000
        return True

    async def remove_terminal(self, run_id: str) -> bool:
        handle = self.get(run_id)
        async with handle.lock:
            if handle.status not in TERMINAL_RUN_STATUSES:
                return False
            handle.subscribers.clear()
            delivery_tasks = tuple(handle.delivery_tasks.values())
            handle.delivery_tasks.clear()
            handle.delivery_queues.clear()
            handle.buffer.clear()
            handle.buffer_bytes = 0
            handle.output_bytes = 0
            handle.limit_error = None
            handle.producer_task = None
            handle.terminal_future = None
            for task in (handle.watchdog_task, handle.hitl_timeout_task):
                if task is not None and task is not asyncio.current_task():
                    task.cancel()
            handle.watchdog_task = None
            handle.hitl_timeout_task = None
            cleanup_task = handle.cleanup_task
            handle.cleanup_task = None
        async with self._registry_lock:
            self._runs.pop(run_id, None)
        self._metrics["terminal_reclaimed"] += 1
        logger.info("agent_run_reclaimed run_id={} status={}", run_id, handle.status.value)
        if cleanup_task is not None and cleanup_task is not asyncio.current_task():
            cleanup_task.cancel()
        for task in delivery_tasks:
            if task is not asyncio.current_task():
                task.cancel()
        if delivery_tasks:
            await asyncio.gather(*delivery_tasks, return_exceptions=True)
        return True

    async def shutdown(self, *, drain_seconds: float = 10.0) -> None:
        tasks = [
            handle.producer_task
            for handle in self._runs.values()
            if handle.producer_task is not None and not handle.producer_task.done()
        ]
        if tasks and drain_seconds > 0:
            _, pending = await asyncio.wait(tasks, timeout=drain_seconds)
        else:
            pending = set(tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        delivery_tasks = [
            task
            for handle in self._runs.values()
            for task in handle.delivery_tasks.values()
            if not task.done()
        ]
        for task in delivery_tasks:
            task.cancel()
        if delivery_tasks:
            await asyncio.gather(*delivery_tasks, return_exceptions=True)

    def record_checkpoint_failure(self, run_id: str) -> None:
        self._metrics["checkpoint_failures"] += 1
        logger.warning("agent_run_checkpoint_failure run_id={}", run_id)

    def metrics_snapshot(self) -> dict[str, int | float]:
        runs = tuple(self._runs.values())
        active = [run for run in runs if run.status not in TERMINAL_RUN_STATUSES]
        subscriber_queues = [queue for run in runs for queue in run.subscribers]
        return {
            **self._metrics,
            "active_runs": len(active),
            "retained_runs": len(runs),
            "event_buffer_events": sum(len(run.buffer) for run in runs),
            "event_buffer_bytes": sum(run.buffer_bytes for run in runs),
            "subscriber_count": len(subscriber_queues),
            "subscriber_queue_events": sum(queue.qsize() for queue in subscriber_queues),
            "subscriber_queue_bytes": sum(
                getattr(queue, "current_bytes", 0) for queue in subscriber_queues
            ),
        }
