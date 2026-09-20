"""worker 内 Run 订阅 hub：远端事件的共享订阅、握手对账与多 Tab fan-out。

一个 worker 进程内，同一 Run 的全部 SSE 订阅共享一份 bus 订阅与一个
reader（task 4.3）：

- **subscribe-first 握手**：先建 bus 订阅并等待 adapter ack（此后发布的
  事件保证可见），再读 DB 权威 snapshot；buffer 中 sequence <= snapshot
  .sequence 的事件已含在 snapshot 内，直接丢弃；
- **gap 对账**：reader 发现 sequence 跳号（> last+1）时重读 snapshot，
  向各 Tab 广播 run-snapshot 置换帧并对齐 last_sequence——Pub/Sub at-most-
  once 丢消息由该路径恢复；
- **旧 term 过滤**：owner_term 低于权威 snapshot term 的迟到事件忽略
  （旧 leader 失锁后的残留发布）；
- **单 Tab 超限只断开自己**：Tab queue 满时丢最旧事件并置哨兵，该 Tab
  的 SSE 由端点收尾（浏览器按既有重连协议重建），其余 Tab 不受影响。

hub 由 RunHubRegistry 管理：首个订阅者到达时创建，最后一个离开时释放
（退订 bus channel）。与本地 RunSubscription 同表面（snapshot / queue /
close 幂等），端点按 queue 产出物分流：本地为 SequencedRunEvent，hub 为
wire dict（与子会话流同形状）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from noesis.runtime.logging import logger

SnapshotLoader = Callable[[str], Awaitable[Any]]
# Tab queue 上限：超限只断开该 Tab（丢最旧 + 哨兵），不阻塞 reader
_TAB_QUEUE_MAX = 512


class HubSubscription:
    """远端订阅句柄：snapshot（握手/对账时的 DB 权威快照）+ wire dict 队列。"""

    def __init__(self, *, snapshot: Any, queue: asyncio.Queue, closer: Callable[[], Awaitable[None]]) -> None:
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "queue", queue)
        object.__setattr__(self, "_closer", closer)
        object.__setattr__(self, "_closed", False)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("HubSubscription 不可变；close 经 _closer 消费")

    async def close(self) -> None:
        """幂等注销：最后一个 Tab 离开时释放整个 hub。"""
        if object.__getattribute__(self, "_closed"):
            return
        object.__setattr__(self, "_closed", True)
        await object.__getattribute__(self, "_closer")()


class RunHub:
    """单 Run 的共享订阅与 fan-out（worker 内单例，经 RunHubRegistry 管理）。"""

    def __init__(
        self,
        *,
        run_id: str,
        bus: Any,
        snapshot_loader: SnapshotLoader,
        on_idle: Callable[[str], Awaitable[None]],
        reconciliation_interval_seconds: float = 30.0,
    ) -> None:
        self._reconciliation_interval = reconciliation_interval_seconds
        self._run_id = run_id
        self._bus = bus
        self._snapshot_loader = snapshot_loader
        self._on_idle = on_idle
        self._tabs: set[asyncio.Queue] = set()
        self._subscription: Any = None
        self._reader: asyncio.Task | None = None
        self._reconcile_task: asyncio.Task | None = None
        self._resync_task: asyncio.Task | None = None
        self._handshake = asyncio.Event()
        self._snapshot: Any = None
        self._last_sequence = 0
        self._owner_term = 0
        self._closed = False

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    async def start(self) -> None:
        """subscribe-first 握手：bus 订阅 ack → DB snapshot → 对齐 last_sequence。"""
        self._subscription = await self._bus.subscribe_run_events(self._run_id)
        await self._subscription.ready()
        await self._align_snapshot()
        self._handshake.set()
        self._reader = asyncio.create_task(
            self._read_loop(), name=f"run-hub-reader:{self._run_id}"
        )
        # 周期对账（task 4.5）：静默期丢失的事件（含终态）在周期内收敛
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name=f"run-hub-reconcile:{self._run_id}"
        )

    async def _align_snapshot(self) -> None:
        snapshot = await self._snapshot_loader(self._run_id)
        if snapshot is None:
            return
        self._snapshot = snapshot
        self._last_sequence = max(self._last_sequence, int(getattr(snapshot, "sequence", 0) or 0))
        self._owner_term = max(self._owner_term, int(getattr(snapshot, "owner_term", 0) or 0))

    async def attach(self) -> HubSubscription:
        """新 Tab 加入：等待握手完成，取当前权威快照。"""
        await self._handshake.wait()
        if self._closed:
            raise RuntimeError(f"run hub closed: {self._run_id}")
        queue: asyncio.Queue = asyncio.Queue(maxsize=_TAB_QUEUE_MAX)
        self._tabs.add(queue)
        snapshot = self._snapshot
        return HubSubscription(
            snapshot=snapshot, queue=queue, closer=self._release_tab(queue)
        )

    def _release_tab(self, queue: asyncio.Queue) -> Callable[[], Awaitable[None]]:
        async def _release() -> None:
            self._tabs.discard(queue)
            if not self._tabs and not self._closed:
                # 最后一个 Tab 离开：整 hub 释放（退订 bus channel）
                await self._on_idle(self._run_id)

        return _release

    async def _read_loop(self) -> None:
        try:
            async for envelope in self._subscription:
                if self._closed:
                    return
                if int(envelope.owner_term) < self._owner_term:
                    continue  # 旧 term 迟到事件
                sequence = int(envelope.sequence)
                if envelope.payload.get("transient"):
                    # 瞬态事件（流式 delta / 实时统计）不占 sequence、重连由
                    # snapshot 全量恢复——直发，不受 sequence 门控
                    self._broadcast(dict(envelope.payload))
                    continue
                if sequence <= self._last_sequence:
                    continue  # 已含在 snapshot / 已对账
                if sequence > self._last_sequence + 1:
                    self._schedule_resync()
                    continue
                self._last_sequence = sequence
                if int(envelope.owner_term) > self._owner_term:
                    self._owner_term = int(envelope.owner_term)
                self._broadcast(dict(envelope.payload))
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closed:
                logger.opt(exception=True).error(
                    "run hub reader 异常退出 run_id={}", self._run_id
                )

    def _schedule_resync(self) -> None:
        if self._resync_task is None or self._resync_task.done():
            self._resync_task = asyncio.create_task(
                self._resync(), name=f"run-hub-resync:{self._run_id}"
            )

    async def _reconcile_loop(self) -> None:
        """周期对账：snapshot 领先本地 last_sequence 时广播置换帧。

        gap 触发的 resync 只在收到后续事件时执行；Pub/Sub 在静默期丢失
        事件（含终态）时无后续事件可触发——周期对账兜底收敛。
        """
        try:
            while not self._closed:
                await asyncio.sleep(self._reconciliation_interval)
                if self._closed:
                    return
                await self._resync()
        except asyncio.CancelledError:
            raise

    async def _resync(self) -> None:
        """gap 对账：重读 DB 权威 snapshot，广播置换帧并对齐 sequence。"""
        try:
            before = self._last_sequence
            await self._align_snapshot()
            if self._last_sequence > before and self._snapshot is not None:
                self._broadcast({"type": "run-snapshot", **self._snapshot.to_dict()})
        except Exception:  # noqa: BLE001
            logger.warning("run hub snapshot 对账失败 run_id={}", self._run_id)

    def _broadcast(self, item: dict) -> None:
        for queue in tuple(self._tabs):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                # 单 Tab 超限只断开自己：丢最旧腾位 + 哨兵（端点收尾、浏览器重连）
                self._tabs.discard(queue)
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
                logger.warning(
                    "run hub tab queue overflow, tab closed run_id={} tabs_left={}",
                    self._run_id, len(self._tabs),
                )

    async def close(self) -> None:
        """释放 hub（registry 调用）：停 reader、退订、向残留 Tab 置哨兵。"""
        if self._closed:
            return
        self._closed = True
        reader = self._reader
        self._reader = None
        if reader is not None and not reader.done():
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
        resync = self._resync_task
        if resync is not None and not resync.done():
            resync.cancel()
        reconcile = self._reconcile_task
        self._reconcile_task = None
        if reconcile is not None and not reconcile.done():
            reconcile.cancel()
            try:
                await reconcile
            except (asyncio.CancelledError, Exception):
                pass
        for queue in tuple(self._tabs):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._tabs.clear()
        if self._subscription is not None:
            try:
                await self._subscription.close()
            except Exception:  # noqa: BLE001
                pass
            self._subscription = None


class RunHubRegistry:
    """进程内 run_id -> RunHub；首个订阅创建，最后一个离开释放。"""

    def __init__(
        self,
        *,
        bus: Any,
        snapshot_loader: SnapshotLoader,
        reconciliation_interval_seconds: float = 30.0,
    ) -> None:
        self._bus = bus
        self._snapshot_loader = snapshot_loader
        self._reconciliation_interval = reconciliation_interval_seconds
        self._hubs: dict[str, RunHub] = {}
        self._lock = asyncio.Lock()

    @property
    def hub_count(self) -> int:
        return len(self._hubs)

    async def subscribe(self, run_id: str) -> HubSubscription:
        async with self._lock:
            hub = self._hubs.get(run_id)
            if hub is None:
                hub = RunHub(
                    run_id=run_id,
                    bus=self._bus,
                    snapshot_loader=self._snapshot_loader,
                    on_idle=self._release,
                    reconciliation_interval_seconds=self._reconciliation_interval,
                )
                self._hubs[run_id] = hub
                await hub.start()
            return await hub.attach()

    async def _release(self, run_id: str) -> None:
        async with self._lock:
            hub = self._hubs.get(run_id)
            if hub is None or hub.tab_count > 0:
                return
            self._hubs.pop(run_id, None)
        await hub.close()

    async def close_all(self) -> None:
        async with self._lock:
            hubs = list(self._hubs.values())
            self._hubs.clear()
        for hub in hubs:
            await hub.close()


__all__ = ["HubSubscription", "RunHub", "RunHubRegistry"]
