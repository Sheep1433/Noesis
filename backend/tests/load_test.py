"""Reliable SSE in-process capacity harness.

默认执行 OpenSpec §7.8 的容量形状：100 active Run、每 Run 2–3 个 Tab、
每 Run 10–30 events/s，并混入慢消费和中途重连。它不依赖模型、登录态或外部服务，
因此结果可重复；测量的是 RunHandle fan-out 到 subscriber dequeue 的真实延迟。

运行：cd backend && uv run python tests/load_test.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import time
import copy
from dataclasses import asdict, dataclass
from pathlib import Path

from noesis.chat.delivery.events import RunCompleted
from noesis.chat.runs import (
    RunManager,
    RunSnapshot,
    RunStatus,
    SlowSubscriber,
    TerminalCommitResult,
)


@dataclass
class LoadReport:
    runs: int
    subscriptions: int
    received_events: int
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    slow_latency_p99_ms: float
    event_loop_lag_ms: float
    rss_mb: float
    queue_bytes: int
    checkpoint_lag_events: int
    subscriber_overflow: int
    reconnect_subscriptions: int
    terminal_delivered_runs: int
    terminal_delivered_subscriptions: int
    reclaimed_runs: int


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * quantile))
    return ordered[index]


def snapshot_provider(run_id: str, user_id: str):
    def build(sequence: int, status: RunStatus, attempt_id: int) -> RunSnapshot:
        return RunSnapshot(
            run_id=run_id,
            user_id=user_id,
            session_id=f"session-{run_id}",
            assistant_message_id=f"message-{run_id}",
            qa_type="COMMON_QA",
            origin="load-test",
            status=status,
            sequence=sequence,
            attempt_id=attempt_id,
        )

    return build


class LoadProjection:
    """容量测试用最小 projection；终态仍走正式 durability barrier。"""

    def __init__(self, run_id: str, user_id: str) -> None:
        self.run_id = run_id
        self.user_id = user_id
        self.status = RunStatus.RUNNING
        self.attempt_id = 1

    def apply(self, event, *, attempt_id: int | None = None) -> bool:
        if attempt_id is not None and attempt_id != self.attempt_id:
            return False
        if isinstance(event, RunCompleted):
            self.status = RunStatus.COMPLETED
        return True

    def clone(self):
        return copy.copy(self)

    def snapshot(self, sequence: int, status: RunStatus, attempt_id: int) -> RunSnapshot:
        return snapshot_provider(self.run_id, self.user_id)(
            sequence,
            self.status if self.status != RunStatus.RUNNING else status,
            attempt_id,
        )


async def run_capacity_test(
    *, runs: int = 100, duration_seconds: float = 5.0, tabs_min: int = 2, tabs_max: int = 3
) -> LoadReport:
    manager = RunManager(
        max_active_runs=runs + 10,
        max_user_active_runs=1,
        max_subscriptions_global=runs * tabs_max + 20,
        max_subscriptions_per_user=tabs_max + 2,
        terminal_retention_seconds=60,
        subscriber_queue_events=32,
    )
    release = asyncio.Event()
    producer_done = asyncio.Event()
    completed_producers = 0
    latencies: list[float] = []
    slow_latencies: list[float] = []
    readers: list[asyncio.Task[None]] = []
    terminal_received_runs: set[str] = set()
    terminal_received_subscriptions: set[str] = set()
    expected_terminal_subscriptions: set[str] = set()
    subscriptions = []
    peaks = {"queue_bytes": 0, "checkpoint_lag": 0, "event_loop_lag": 0.0}
    sampling = True

    async def sample_metrics() -> None:
        while sampling:
            current = manager.metrics_snapshot()
            peaks["queue_bytes"] = max(
                int(peaks["queue_bytes"]), int(current["subscriber_queue_bytes"])
            )
            peaks["checkpoint_lag"] = max(
                int(peaks["checkpoint_lag"]), int(current["checkpoint_lag_events"])
            )
            peaks["event_loop_lag"] = max(
                float(peaks["event_loop_lag"]), float(current["event_loop_lag_last_ms"])
            )
            await asyncio.sleep(0.05)

    async def checkpoint_handler(_request) -> None:
        await asyncio.sleep(0.001)

    async def terminal_handler(_candidate) -> TerminalCommitResult:
        await asyncio.sleep(0.001)
        return TerminalCommitResult("committed")

    async def consume(subscription, *, slow: bool, subscriber_id: str) -> None:
        while True:
            item = await subscription.queue.get()
            try:
                if isinstance(item, SlowSubscriber):
                    return
                latency = (time.monotonic() - item.created_at_monotonic) * 1000
                (slow_latencies if slow else latencies).append(latency)
                manager.record_event_delivered(item)
                if isinstance(item.event, RunCompleted):
                    terminal_received_runs.add(item.run_id)
                    terminal_received_subscriptions.add(subscriber_id)
                    return
                if slow:
                    await asyncio.sleep(0.08)
            finally:
                subscription.queue.task_done()

    handles = []
    for index in range(runs):
        run_id = f"load-{index}"
        rate = 10 + index % 21

        async def producer(publish, *, event_rate=rate, own_run_id=run_id) -> None:
            nonlocal completed_producers
            await release.wait()
            interval = 1 / event_rate
            deadline = asyncio.get_running_loop().time() + duration_seconds
            sequence = 0
            while asyncio.get_running_loop().time() < deadline:
                sequence += 1
                await publish({"type": "text-delta", "run": own_run_id, "n": sequence})
                await asyncio.sleep(interval)
            await publish(RunCompleted(finish_reason="stop"), 1)
            completed_producers += 1
            if completed_producers == runs:
                producer_done.set()

        projection = LoadProjection(run_id, f"user-{index}")
        handle = await manager.start(
            run_id=run_id,
            session_id=f"session-{index}",
            user_id=f"user-{index}",
            assistant_message_id=f"message-{index}",
            snapshot_provider=projection.snapshot,
            producer=producer,
            state=projection,
            checkpoint_policy=lambda _event, sequence: (
                "coalescible" if sequence % 10 == 0 else None
            ),
            checkpoint_handler=checkpoint_handler,
            terminal_handler=terminal_handler,
        )
        handles.append(handle)
        tab_count = tabs_min + index % (tabs_max - tabs_min + 1)
        for tab in range(tab_count):
            subscription = await manager.subscribe(run_id)
            subscriber_id = f"{run_id}:tab-{tab}"
            is_slow = tab == tab_count - 1 and index % 10 == 0
            subscriptions.append((run_id, subscription, subscriber_id, is_slow))
            if not is_slow:
                expected_terminal_subscriptions.add(subscriber_id)
            readers.append(
                asyncio.create_task(
                    consume(
                        subscription,
                        slow=is_slow,
                        subscriber_id=subscriber_id,
                    )
                )
            )

    sampler = asyncio.create_task(sample_metrics())
    release.set()

    async def reconnect_some_tabs() -> None:
        await asyncio.sleep(duration_seconds / 2)
        for run_id, subscription, subscriber_id, _is_slow in subscriptions[::25]:
            await manager.unsubscribe(run_id, subscription.queue)
            expected_terminal_subscriptions.discard(subscriber_id)
            replacement = await manager.subscribe(
                run_id, after_sequence=manager.get(run_id).last_sequence
            )
            replacement_id = f"{subscriber_id}:reconnect"
            expected_terminal_subscriptions.add(replacement_id)
            readers.append(
                asyncio.create_task(
                    consume(
                        replacement,
                        slow=False,
                        subscriber_id=replacement_id,
                    )
                )
            )

    reconnect_task = asyncio.create_task(reconnect_some_tabs())
    await asyncio.wait_for(producer_done.wait(), timeout=duration_seconds + 10)
    await asyncio.gather(*(handle.producer_task for handle in handles))
    await reconnect_task
    await asyncio.gather(*(manager.drain_persistence(handle.run_id) for handle in handles))
    await asyncio.sleep(0.2)
    sampling = False
    await sampler

    metrics_before_reclaim = manager.metrics_snapshot()
    assert len(terminal_received_runs) == runs, (
        f"only {len(terminal_received_runs)}/{runs} runs delivered a committed terminal"
    )
    missing_terminals = expected_terminal_subscriptions - terminal_received_subscriptions
    assert not missing_terminals, (
        f"normal subscribers missing committed terminal: {sorted(missing_terminals)[:5]}"
    )
    for task in readers:
        task.cancel()
    await asyncio.gather(*readers, return_exceptions=True)
    for handle in handles:
        await manager.remove_terminal(handle.run_id)
    metrics = manager.metrics_snapshot()
    await manager.shutdown(drain_seconds=0)

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss / (1024 * 1024) if rss > 10_000_000 else rss / 1024
    return LoadReport(
        runs=runs,
        subscriptions=len(subscriptions),
        received_events=len(latencies),
        latency_p50_ms=round(percentile(latencies, 0.50), 3),
        latency_p95_ms=round(percentile(latencies, 0.95), 3),
        latency_p99_ms=round(percentile(latencies, 0.99), 3),
        slow_latency_p99_ms=round(percentile(slow_latencies, 0.99), 3),
        event_loop_lag_ms=round(float(peaks["event_loop_lag"]), 3),
        rss_mb=round(rss_mb, 2),
        queue_bytes=int(peaks["queue_bytes"]),
        checkpoint_lag_events=int(peaks["checkpoint_lag"]),
        subscriber_overflow=int(metrics_before_reclaim["subscriber_overflow"]),
        reconnect_subscriptions=int(metrics_before_reclaim["reconnect_subscriptions"]),
        terminal_delivered_runs=len(terminal_received_runs),
        terminal_delivered_subscriptions=len(terminal_received_subscriptions),
        reclaimed_runs=int(metrics["terminal_reclaimed"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_capacity_test(runs=args.runs, duration_seconds=args.duration))
    payload = json.dumps(asdict(report), ensure_ascii=False, indent=2)
    print(payload)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{payload}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
