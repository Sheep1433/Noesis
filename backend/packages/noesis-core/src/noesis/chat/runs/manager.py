"""进程内 RunManager：producer 与 Delivery 生命周期分离。"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from noesis.runtime.logging import logger
from noesis.chat.delivery.events import (
    RunAborted,
    RunCompleted,
    RunError,
    RunSnapshotReplaced,
)
from noesis.chat.runs.models import (
    ACTIVE_RUN_STATUSES,
    RunSnapshot,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    require_transition,
)
from noesis.chat.runs.session_signals import session_signal_bus


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


class StaleProducerGeneration(RuntimeError):
    pass


class SubscriptionLimitExceeded(RuntimeError):
    error_code = "SSE_SUBSCRIPTION_LIMIT"


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
    checkpoint_snapshot: RunSnapshot | None = None
    checkpoint_kind: str | None = None
    created_at_monotonic: float = field(default_factory=time.monotonic)

    @property
    def estimated_bytes(self) -> int:
        try:
            return len(json.dumps(self.event, default=str, ensure_ascii=False).encode("utf-8")) + 64
        except (TypeError, ValueError):
            return len(repr(self.event).encode("utf-8")) + 64


@dataclass(frozen=True)
class CheckpointRequest:
    """PersistWriter 的不可变 checkpoint 请求。snapshot 在 lock 内捕获，与 snapshot_sequence 绑定。"""

    run_id: str
    assistant_message_id: str
    snapshot_sequence: int
    snapshot: RunSnapshot
    kind: str  # coalescible | semantic | terminal


@dataclass(frozen=True)
class TerminalCandidate:
    """尚未对 live state 可见的 immutable terminal projection。"""

    envelope: SequencedRunEvent
    snapshot: RunSnapshot
    status: RunStatus
    projected_state: Any


@dataclass(frozen=True)
class TerminalCommitResult:
    """terminal repository transaction 的领域化结果。"""

    outcome: str  # committed | already_finalized | failed
    snapshot: RunSnapshot | None = None


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
CheckpointPolicy = Callable[[Any, int], str | None]
CheckpointHandler = Callable[[CheckpointRequest], Awaitable[None]]
TerminalHandler = Callable[[TerminalCandidate], Awaitable[TerminalCommitResult]]


class PersistWriter:
    """每 Run 单槽 latest-wins checkpoint writer，不参与 subscriber fan-out。"""

    def __init__(
        self,
        handler: CheckpointHandler,
        *,
        retry_interval_seconds: float = 1.0,
        on_blocked: Callable[[BaseException], None] | None = None,
        on_persisted: Callable[[CheckpointRequest, float], None] | None = None,
    ) -> None:
        self._handler = handler
        self._retry_interval_seconds = retry_interval_seconds
        self._on_blocked = on_blocked
        self._on_persisted = on_persisted
        self._pending: CheckpointRequest | None = None
        self._wakeup = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._closed = False
        self._task = asyncio.create_task(self._run(), name="agent-run-persist-writer")

    def submit(self, request: CheckpointRequest) -> bool:
        if self._closed:
            return False
        if self._pending is not None and self._pending.snapshot_sequence >= request.snapshot_sequence:
            return False
        self._pending = request
        self._idle.clear()
        self._wakeup.set()
        return True

    def discard_through(self, sequence: int) -> None:
        if self._pending is not None and self._pending.snapshot_sequence <= sequence:
            self._pending = None
        if self._pending is None:
            self._idle.set()

    async def drain(self) -> None:
        await self._idle.wait()

    async def close(self) -> None:
        self._closed = True
        self._pending = None
        self._wakeup.set()
        if self._task is not asyncio.current_task():
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            if self._closed and self._pending is None:
                self._idle.set()
                return
            request = self._pending
            self._pending = None
            if request is None:
                self._idle.set()
                continue
            try:
                started = time.monotonic()
                await self._handler(request)
                if self._on_persisted is not None:
                    self._on_persisted(request, (time.monotonic() - started) * 1000)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                if self._on_blocked is not None:
                    self._on_blocked(exc)
                if self._pending is None or self._pending.snapshot_sequence < request.snapshot_sequence:
                    self._pending = request
                await asyncio.sleep(self._retry_interval_seconds)
            if self._pending is not None:
                self._wakeup.set()
            else:
                self._idle.set()


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
    producer_generation: int = 0
    cancel_requested: bool = False
    next_sequence: int = 1
    buffer: deque[SequencedRunEvent] = field(default_factory=deque)
    buffer_bytes: int = 0
    subscribers: set[asyncio.Queue[SequencedRunEvent | SlowSubscriber]] = field(default_factory=set)
    sse_subscribers: set[asyncio.Queue[SequencedRunEvent | SlowSubscriber]] = field(
        default_factory=set
    )
    delivery_queues: dict[str, asyncio.Queue[SequencedRunEvent | SlowSubscriber]] = field(
        default_factory=dict
    )
    delivery_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    delivery_failures: dict[str, BaseException] = field(default_factory=dict)
    terminal_future: asyncio.Future[RunStatus] | None = None
    max_run_duration_seconds: float = 0.0
    cleanup_task: asyncio.Task[None] | None = None
    watchdog_task: asyncio.Task[None] | None = None
    hitl_timeout_task: asyncio.Task[None] | None = None
    output_bytes: int = 0
    limit_error: RunLimitExceeded | None = None
    limit_handler: LimitHandler | None = None
    checkpoint_policy: CheckpointPolicy | None = None
    terminal_handler: TerminalHandler | None = None
    persist_writer: PersistWriter | None = None
    pending_terminal: TerminalCandidate | None = None
    terminal_retry_task: asyncio.Task[None] | None = None
    authoritative_snapshot: RunSnapshot | None = None
    last_persisted_sequence: int = 0
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
        max_subscriptions_per_run: int = 8,
        max_subscriptions_per_user: int = 16,
        max_subscriptions_global: int = 500,
        terminal_retention_seconds: float = 300.0,
        max_run_duration_seconds: float = 900.0,
        max_output_bytes: int = 64 * 1024 * 1024,
        hitl_pending_timeout_seconds: float = 86400.0,
        cancel_grace_seconds: float = 2.0,
        terminal_persistence_budget_seconds: float = 5.0,
        terminal_retry_interval_seconds: float = 5.0,
        checkpoint_retry_interval_seconds: float = 0.25,
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
        self.max_subscriptions_per_run = max_subscriptions_per_run
        self.max_subscriptions_per_user = max_subscriptions_per_user
        self.max_subscriptions_global = max_subscriptions_global
        self.terminal_retention_seconds = terminal_retention_seconds
        self.max_run_duration_seconds = max_run_duration_seconds
        self.max_output_bytes = max_output_bytes
        self.hitl_pending_timeout_seconds = hitl_pending_timeout_seconds
        self.cancel_grace_seconds = cancel_grace_seconds
        self.terminal_persistence_budget_seconds = terminal_persistence_budget_seconds
        self.terminal_retry_interval_seconds = terminal_retry_interval_seconds
        self.checkpoint_retry_interval_seconds = checkpoint_retry_interval_seconds
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
            "stale_producer_generation_events": 0,
            "delivery_failures": 0,
            "terminal_cas_loser": 0,
            "persistence_blocked": 0,
            "subscription_limit_rejected": 0,
            "checkpoint_coalesced": 0,
            "checkpoint_latency_last_ms": 0.0,
            "checkpoint_lag_events": 0,
            "event_loop_lag_last_ms": 0.0,
            "event_to_client_latency_last_ms": 0.0,
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
        checkpoint_policy: CheckpointPolicy | None = None,
        terminal_handler: TerminalHandler | None = None,
        checkpoint_handler: CheckpointHandler | None = None,
        max_run_duration_seconds: float | None = None,
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
            max_run_duration_seconds=(
                max_run_duration_seconds
                if max_run_duration_seconds is not None
                else self.max_run_duration_seconds
            ),
            state=state,
            limit_handler=limit_handler,
            checkpoint_policy=checkpoint_policy,
            terminal_handler=terminal_handler,
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
        if checkpoint_handler is not None:
            def on_persistence_blocked(exc: BaseException) -> None:
                self._metrics["persistence_blocked"] += 1
                logger.error(
                    "agent_run_persistence_blocked run_id={} error_type={}",
                    run_id,
                    type(exc).__name__,
                )
                task = handle.producer_task
                if task is not None and not task.done():
                    task.cancel()

            handle.persist_writer = PersistWriter(
                checkpoint_handler,
                retry_interval_seconds=self.checkpoint_retry_interval_seconds,
                on_blocked=on_persistence_blocked,
                on_persisted=lambda request, latency_ms: self._record_checkpoint_persisted(
                    handle, request, latency_ms
                ),
            )
        logger.info(
            "agent_run_registered run_id={} session_id={} assistant_message_id={} attempt_id={} status=running",
            run_id,
            session_id,
            assistant_message_id,
            attempt_id,
        )
        async with handle.lock:
            handle.status = RunStatus.RUNNING
            generation = self._begin_producer_segment_locked(handle)
            self._publish_session_signal(handle, RunStatus.RUNNING)
        handle.producer_task = asyncio.create_task(
            self._run_producer(handle, producer, generation), name=f"agent-run:{run_id}"
        )
        handle.watchdog_task = asyncio.create_task(
            self._expire_running_run(handle), name=f"agent-run-timeout:{run_id}"
        )
        return handle

    def _publish_session_signal(self, handle: RunHandle, target: RunStatus) -> None:
        """状态迁移后向 session 信令总线投递 hint（run-started / hitl-pending / terminal）。

        start()/resume() 直接置 RUNNING 不走 transition()，须各自调用；
        信令幂等，重复投递无害——客户端只据它去拉权威状态。
        """
        if target == RunStatus.RUNNING:
            signal = {
                "type": "run-started",
                "run_id": handle.run_id,
                "assistant_message_id": handle.assistant_message_id,
            }
        elif target == RunStatus.HITL_PENDING:
            signal = {"type": "run-hitl-pending", "run_id": handle.run_id}
        elif target in TERMINAL_RUN_STATUSES:
            signal = {"type": "run-terminal", "run_id": handle.run_id, "status": target.value}
        else:
            return
        session_signal_bus.publish(handle.user_id, handle.session_id, signal)

    def _sample_event_loop_lag(self) -> None:
        loop = asyncio.get_running_loop()
        scheduled_at = loop.time()

        def record() -> None:
            self._metrics["event_loop_lag_last_ms"] = max(
                0.0, (loop.time() - scheduled_at) * 1000
            )

        loop.call_soon(record)

    def _record_checkpoint_persisted(
        self,
        handle: RunHandle,
        request: CheckpointRequest,
        latency_ms: float,
    ) -> None:
        handle.last_persisted_sequence = max(
            handle.last_persisted_sequence, request.snapshot_sequence
        )
        self._metrics["checkpoint_latency_last_ms"] = latency_ms
        self._metrics["checkpoint_lag_events"] = max(
            0, handle.last_sequence - handle.last_persisted_sequence
        )

    async def _expire_running_run(self, handle: RunHandle) -> None:
        await asyncio.sleep(handle.max_run_duration_seconds)
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

    async def resume(
        self,
        run_id: str,
        producer: Producer,
        *,
        prepare: ResumePrepare | None = None,
    ) -> RunHandle:
        handle = self.get(run_id)
        self._sample_event_loop_lag()
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
            generation = self._begin_producer_segment_locked(handle)
            self._publish_session_signal(handle, RunStatus.RUNNING)
            handle.producer_task = asyncio.create_task(
                self._run_producer(handle, producer, generation), name=f"agent-run-resume:{run_id}"
            )
            if handle.watchdog_task is None or handle.watchdog_task.done():
                handle.watchdog_task = asyncio.create_task(
                    self._expire_running_run(handle), name=f"agent-run-timeout:{run_id}"
                )
            return handle

    @staticmethod
    def _begin_producer_segment_locked(handle: RunHandle) -> int:
        """调用方持有 handle.lock 时递增 producer generation。"""
        handle.producer_generation += 1
        return handle.producer_generation

    async def _run_producer(self, handle: RunHandle, producer: Producer, generation: int) -> None:
        try:
            await producer(
                lambda event, attempt_id=None: self.apply_event(
                    handle.run_id, event, producer_generation=generation, attempt_id=attempt_id
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise

    def get(self, run_id: str) -> RunHandle:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFound(run_id) from exc

    def list_active_for_user(self, user_id: str) -> list[RunHandle]:
        """用户的活跃 run（含 hitl_pending），供 /status 等只读命令查询。"""
        uid = str(user_id)
        return [
            h
            for h in self._runs.values()
            if h.user_id == uid and h.status in ACTIVE_RUN_STATUSES
        ]

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
                if handle.watchdog_task is not None and handle.watchdog_task is not asyncio.current_task():
                    handle.watchdog_task.cancel()
                    handle.watchdog_task = None
            if target in TERMINAL_RUN_STATUSES and handle.terminal_future is not None:
                self._mark_terminal_locked(handle, target)
            self._publish_session_signal(handle, target)
            return True

    def _mark_terminal_locked(self, handle: RunHandle, target: RunStatus) -> None:
        for task in (handle.watchdog_task, handle.hitl_timeout_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        handle.watchdog_task = None
        handle.hitl_timeout_task = None
        if handle.terminal_future is not None and not handle.terminal_future.done():
            handle.terminal_future.set_result(target)
        if handle.cleanup_task is None:
            handle.cleanup_task = asyncio.create_task(
                self._cleanup_after_retention(handle.run_id)
            )

    async def _cleanup_after_retention(self, run_id: str) -> None:
        if self.terminal_retention_seconds > 0:
            await asyncio.sleep(self.terminal_retention_seconds)
        try:
            await self.remove_terminal(run_id)
        except RunNotFound:
            pass

    async def publish(self, run_id: str, event: Any) -> SequencedRunEvent:
        return await self.publish_attempt(run_id, event)

    def _assign_and_buffer(
        self, handle: RunHandle, event: Any
    ) -> SequencedRunEvent:
        """在 lock 内分配 sequence、写 buffer。无 I/O await，不 fan-out。

        如果 checkpoint_policy 命中，在同一 lock 内复制 immutable snapshot 并附加到 envelope。
        """
        checkpoint_snapshot: RunSnapshot | None = None
        checkpoint_kind: str | None = None
        if handle.checkpoint_policy is not None:
            sequence = handle.next_sequence
            checkpoint_kind = handle.checkpoint_policy(event, sequence)
            if checkpoint_kind is not None:
                checkpoint_snapshot = copy.deepcopy(
                    handle.snapshot_provider(sequence, handle.status, handle.attempt_id)
                )
        envelope = SequencedRunEvent(
            run_id=handle.run_id,
            sequence=handle.next_sequence,
            attempt_id=handle.attempt_id,
            event=event,
            checkpoint_snapshot=checkpoint_snapshot,
            checkpoint_kind=checkpoint_kind,
        )
        # 即使普通输出已经触及上限，也必须允许 RunError / RunAborted
        # 投递终态，否则 producer 已经被限流后，终态事件还会再次触发同一个异常。
        terminal_event = isinstance(event, (RunCompleted, RunAborted, RunError))
        if handle.output_bytes + envelope.estimated_bytes > self.max_output_bytes and not terminal_event:
            handle.limit_error = RunOutputExceeded("run output limit exceeded")
            raise handle.limit_error
        handle.output_bytes += envelope.estimated_bytes
        self._metrics["published_events"] += 1
        self._metrics["published_bytes"] += envelope.estimated_bytes
        handle.next_sequence += 1
        handle.buffer.append(envelope)
        handle.buffer_bytes += envelope.estimated_bytes
        self._trim_buffer(handle)
        return envelope

    def _fanout(self, handle: RunHandle, envelope: SequencedRunEvent) -> None:
        """在 lock 内向 subscriber queue 非阻塞投递 envelope。"""
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
                handle.run_id,
                envelope.sequence,
                queue.qsize(),
                getattr(queue, "current_bytes", 0),
            )
            handle.subscribers.discard(queue)
            handle.sse_subscribers.discard(queue)
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(SlowSubscriber("subscriber queue overflow"))
            except asyncio.QueueFull:
                pass

    def _assign_and_fanout(
        self, handle: RunHandle, event: Any
    ) -> SequencedRunEvent:
        """在 lock 内分配 sequence、写 buffer、fan-out。无 I/O await。"""
        envelope = self._assign_and_buffer(handle, event)
        self._fanout(handle, envelope)
        return envelope

    async def apply_event(
        self,
        run_id: str,
        event: Any,
        *,
        producer_generation: int | None = None,
        attempt_id: int | None = None,
    ) -> SequencedRunEvent | None:
        """原子地 reduce projection 并分配 sequence、fan-out。

        projection.apply、sequence 分配、buffer 写入和 subscriber fan-out 在同一个
        lock 临界区内完成，不发生 await I/O。保证 snapshot sequence 与 projection
        内容一致。如果 projection.apply 返回 False（如 StreamDone + HITL_PENDING），
        返回 None 且不分配 sequence。
        """
        handle = self.get(run_id)
        self._sample_event_loop_lag()
        terminal_candidate: TerminalCandidate | None = None
        async with handle.lock:
            if handle.status in TERMINAL_RUN_STATUSES:
                return None
            if handle.pending_terminal is not None:
                return None
            if producer_generation is not None and producer_generation != handle.producer_generation:
                self._metrics["stale_producer_generation_events"] += 1
                logger.warning(
                    "agent_run_stale_producer_generation run_id={} event_gen={} current_gen={}",
                    run_id,
                    producer_generation,
                    handle.producer_generation,
                )
                raise StaleProducerGeneration(
                    f"stale producer generation: run={run_id} gen={producer_generation} current={handle.producer_generation}"
                )
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
            projection = handle.state
            if projection is not None and hasattr(projection, "apply"):
                is_terminal_intent = (
                    hasattr(projection, "status")
                    and projection.status not in TERMINAL_RUN_STATUSES
                    and isinstance(event, (RunCompleted, RunAborted, RunError))
                )
                if is_terminal_intent:
                    if handle.pending_terminal is not None:
                        return handle.pending_terminal.envelope
                    projected_state = (
                        projection.clone()
                        if hasattr(projection, "clone")
                        else copy.deepcopy(projection)
                    )
                    applied = projected_state.apply(event, attempt_id=attempt_id)
                    if not applied:
                        return None
                    terminal_snapshot = copy.deepcopy(
                        projected_state.snapshot(
                            handle.next_sequence,
                            projected_state.status,
                            projected_state.attempt_id,
                        )
                    )
                    envelope = SequencedRunEvent(
                        run_id=handle.run_id,
                        sequence=handle.next_sequence,
                        attempt_id=projected_state.attempt_id,
                        event=event,
                        checkpoint_snapshot=terminal_snapshot,
                    )
                    # 终态事件不受普通输出累计上限阻断，确保超限后仍能完成状态机收尾。
                    if (
                        handle.output_bytes + envelope.estimated_bytes > self.max_output_bytes
                        and not isinstance(event, (RunCompleted, RunAborted, RunError))
                    ):
                        handle.limit_error = RunOutputExceeded("run output limit exceeded")
                        raise handle.limit_error
                    terminal_candidate = TerminalCandidate(
                        envelope=envelope,
                        snapshot=terminal_snapshot,
                        status=projected_state.status,
                        projected_state=projected_state,
                    )
                    handle.pending_terminal = terminal_candidate
                    logger.info(
                        "agent_run_terminal_candidate run_id={} sequence={} status={}",
                        run_id,
                        envelope.sequence,
                        projected_state.status.value,
                    )
                else:
                    applied = projection.apply(event, attempt_id=attempt_id)
                    if not applied:
                        return None
                    handle.status = projection.status
                    handle.attempt_id = projection.attempt_id
            if terminal_candidate is not None:
                envelope = terminal_candidate.envelope
            else:
                envelope = self._assign_and_buffer(handle, event)
                self._fanout(handle, envelope)
                if (
                    envelope.checkpoint_snapshot is not None
                    and handle.persist_writer is not None
                ):
                    submitted = handle.persist_writer.submit(
                        CheckpointRequest(
                            run_id=handle.run_id,
                            assistant_message_id=handle.assistant_message_id,
                            snapshot_sequence=envelope.sequence,
                            snapshot=envelope.checkpoint_snapshot,
                            kind=envelope.checkpoint_kind or "coalescible",
                        )
                    )
                    if not submitted:
                        self._metrics["checkpoint_coalesced"] += 1
                    self._metrics["checkpoint_lag_events"] = max(
                        0, handle.last_sequence - handle.last_persisted_sequence
                    )
        if terminal_candidate is not None and handle.terminal_handler is not None:
            await self._persist_terminal_with_budget(handle, terminal_candidate)
        return envelope

    async def _persist_terminal_with_budget(
        self, handle: RunHandle, candidate: TerminalCandidate
    ) -> TerminalCommitResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.terminal_persistence_budget_seconds
        result = TerminalCommitResult("failed")
        while loop.time() < deadline:
            result = await self._commit_terminal_candidate(handle, candidate)
            if result.outcome != "failed":
                return result
            await asyncio.sleep(min(0.1, max(0.0, deadline - loop.time())))
        async with handle.lock:
            if handle.pending_terminal is candidate and handle.terminal_retry_task is None:
                self._metrics["persistence_blocked"] += 1
                handle.cancel_requested = True
                handle.terminal_retry_task = asyncio.create_task(
                    self._retry_terminal(handle, candidate),
                    name=f"agent-run-terminal-retry:{handle.run_id}",
                )
                producer_task = handle.producer_task
            else:
                producer_task = None
        if producer_task is not None and not producer_task.done():
            producer_task.cancel()
        return result

    async def _retry_terminal(
        self, handle: RunHandle, candidate: TerminalCandidate
    ) -> None:
        try:
            while True:
                await asyncio.sleep(self.terminal_retry_interval_seconds)
                result = await self._commit_terminal_candidate(handle, candidate)
                if result.outcome != "failed":
                    return
        finally:
            async with handle.lock:
                if handle.terminal_retry_task is asyncio.current_task():
                    handle.terminal_retry_task = None

    async def _commit_terminal_candidate(
        self, handle: RunHandle, candidate: TerminalCandidate
    ) -> TerminalCommitResult:
        handler = handle.terminal_handler
        if handler is None:
            return TerminalCommitResult("failed")
        try:
            result = await handler(candidate)
        except Exception:
            logger.exception(
                "agent_run_terminal_persist_failed run_id={} sequence={}",
                handle.run_id,
                candidate.envelope.sequence,
            )
            result = TerminalCommitResult("failed")

        async with handle.lock:
            if handle.pending_terminal is not candidate:
                return result
            if result.outcome == "committed":
                if handle.persist_writer is not None:
                    handle.persist_writer.discard_through(candidate.envelope.sequence)
                handle.state = candidate.projected_state
                handle.status = candidate.status
                handle.attempt_id = candidate.envelope.attempt_id
                handle.next_sequence = candidate.envelope.sequence + 1
                handle.output_bytes += candidate.envelope.estimated_bytes
                self._metrics["published_events"] += 1
                self._metrics["published_bytes"] += candidate.envelope.estimated_bytes
                handle.buffer.append(candidate.envelope)
                handle.buffer_bytes += candidate.envelope.estimated_bytes
                self._trim_buffer(handle)
                self._fanout(handle, candidate.envelope)
                handle.pending_terminal = None
                self._mark_terminal_locked(handle, candidate.status)
            elif result.outcome == "already_finalized" and result.snapshot is not None:
                self._metrics["terminal_cas_loser"] += 1
                handle.authoritative_snapshot = copy.deepcopy(result.snapshot)
                handle.status = result.snapshot.status
                handle.attempt_id = result.snapshot.attempt_id
                handle.next_sequence = result.snapshot.sequence + 1
                handle.pending_terminal = None
                replacement = SequencedRunEvent(
                    run_id=handle.run_id,
                    sequence=result.snapshot.sequence,
                    attempt_id=result.snapshot.attempt_id,
                    event=RunSnapshotReplaced(result.snapshot.to_dict()),
                )
                self._fanout(handle, replacement)
                self._mark_terminal_locked(handle, result.snapshot.status)
        return result

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
            return self._assign_and_fanout(handle, event)

    def _trim_buffer(self, handle: RunHandle) -> None:
        while handle.buffer and (
            len(handle.buffer) > self.max_buffer_events
            or handle.buffer_bytes > self.max_buffer_bytes
        ):
            removed = handle.buffer.popleft()
            handle.buffer_bytes -= removed.estimated_bytes

    def _check_subscription_quota(self, handle: RunHandle) -> None:
        """检查 SSE subscription 配额：per-run、per-user、global。超限抛 SubscriptionLimitExceeded。"""
        run_subs = len(handle.sse_subscribers)
        if run_subs >= self.max_subscriptions_per_run:
            self._metrics["subscription_limit_rejected"] += 1
            raise SubscriptionLimitExceeded(
                f"per-run subscription limit exceeded: run={handle.run_id} "
                f"current={run_subs} max={self.max_subscriptions_per_run}"
            )
        user_subs = sum(
            len(h.sse_subscribers)
            for h in self._runs.values()
            if h.user_id == handle.user_id
        )
        if user_subs >= self.max_subscriptions_per_user:
            self._metrics["subscription_limit_rejected"] += 1
            raise SubscriptionLimitExceeded(
                f"per-user subscription limit exceeded: user={handle.user_id} "
                f"current={user_subs} max={self.max_subscriptions_per_user}"
            )
        global_subs = sum(len(h.sse_subscribers) for h in self._runs.values())
        if global_subs >= self.max_subscriptions_global:
            self._metrics["subscription_limit_rejected"] += 1
            raise SubscriptionLimitExceeded(
                f"global subscription limit exceeded: "
                f"current={global_subs} max={self.max_subscriptions_global}"
            )

    async def subscribe(self, run_id: str, *, after_sequence: int = 0) -> RunSubscription:
        handle = self.get(run_id)
        async with self._registry_lock:
            async with handle.lock:
                self._check_subscription_quota(handle)
                return self._subscribe_locked(handle, after_sequence)

    def _subscribe_locked(
        self, handle: RunHandle, after_sequence: int
    ) -> RunSubscription:
        if after_sequence > 0:
            self._metrics["reconnect_subscriptions"] += 1
        queue: asyncio.Queue[SequencedRunEvent | SlowSubscriber] = BoundedEventQueue(
            maxsize=self.subscriber_queue_events,
            max_bytes=self.subscriber_queue_bytes,
        )
        handle.subscribers.add(queue)
        handle.sse_subscribers.add(queue)
        if handle.authoritative_snapshot is not None:
            snapshot = copy.deepcopy(handle.authoritative_snapshot)
        else:
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
            handle.sse_subscribers.discard(queue)

    async def stop(self, run_id: str) -> bool:
        handle = self.get(run_id)
        async with handle.lock:
            if handle.status in TERMINAL_RUN_STATUSES:
                return False
            first_request = not handle.cancel_requested
            handle.cancel_requested = True
            task = handle.producer_task if first_request else None
            terminal_future = handle.terminal_future
            if first_request and handle.state is not None and hasattr(handle.state, "cancel_requested"):
                handle.state.cancel_requested = True
        started = asyncio.get_running_loop().time()
        if task is not None and not task.done():
            task.cancel()
        if terminal_future is not None and not terminal_future.done():
            await asyncio.wait({terminal_future}, timeout=self.cancel_grace_seconds)
        if first_request:
            self._metrics["cancel_count"] += 1
            self._metrics["cancel_latency_last_ms"] = (
                asyncio.get_running_loop().time() - started
            ) * 1000
        return first_request

    async def drain_persistence(self, run_id: str) -> None:
        writer = getattr(self.get(run_id), "persist_writer", None)
        if writer is not None:
            await writer.drain()

    async def remove_terminal(self, run_id: str) -> bool:
        handle = self.get(run_id)
        async with handle.lock:
            if handle.status not in TERMINAL_RUN_STATUSES:
                return False
            handle.subscribers.clear()
            handle.sse_subscribers.clear()
            delivery_tasks = tuple(handle.delivery_tasks.values())
            handle.delivery_tasks.clear()
            handle.delivery_queues.clear()
            handle.buffer.clear()
            handle.buffer_bytes = 0
            handle.output_bytes = 0
            handle.limit_error = None
            handle.producer_task = None
            handle.terminal_future = None
            for task in (handle.watchdog_task, handle.hitl_timeout_task, handle.terminal_retry_task):
                if task is not None and task is not asyncio.current_task():
                    task.cancel()
            handle.watchdog_task = None
            handle.hitl_timeout_task = None
            cleanup_task = handle.cleanup_task
            handle.cleanup_task = None
            persist_writer = handle.persist_writer
            handle.persist_writer = None
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
        if persist_writer is not None:
            await persist_writer.close()
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
        writers = [
            handle.persist_writer
            for handle in self._runs.values()
            if handle.persist_writer is not None
        ]
        if writers:
            await asyncio.gather(*(writer.close() for writer in writers), return_exceptions=True)
        retry_tasks = [
            handle.terminal_retry_task
            for handle in self._runs.values()
            if handle.terminal_retry_task is not None and not handle.terminal_retry_task.done()
        ]
        for task in retry_tasks:
            task.cancel()
        if retry_tasks:
            await asyncio.gather(*retry_tasks, return_exceptions=True)

    def record_checkpoint_failure(self, run_id: str) -> None:
        self._metrics["checkpoint_failures"] += 1
        logger.warning("agent_run_checkpoint_failure run_id={}", run_id)

    def record_event_delivered(self, envelope: SequencedRunEvent) -> None:
        self._metrics["event_to_client_latency_last_ms"] = max(
            0.0, (time.monotonic() - envelope.created_at_monotonic) * 1000
        )

    def metrics_snapshot(self) -> dict[str, int | float]:
        runs = tuple(self._runs.values())
        active = [run for run in runs if run.status not in TERMINAL_RUN_STATUSES]
        subscriber_queues = [queue for run in runs for queue in run.sse_subscribers]
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
