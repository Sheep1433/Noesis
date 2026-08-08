from __future__ import annotations

import asyncio

import pytest

from noesis.domain.chat.delivery.channel_worker import ChannelDeliveryWorker


class _BlockingOutbound:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.received = []
        self.finalized = False

    async def feed_events(self, events):
        self.received.append(events)
        await self.release.wait()

    async def finalize(self):
        self.finalized = True


@pytest.mark.asyncio
async def test_channel_queue_overflow_does_not_block_producer() -> None:
    outbound = _BlockingOutbound()
    failures = []

    async def on_failure(code: str):
        failures.append(code)

    worker = ChannelDeliveryWorker(
        outbound,
        max_batches=1,
        max_bytes=4096,
        drain_seconds=0.05,
        on_failure=on_failure,
    )
    assert await worker.submit([{"type": "text-delta", "delta": "one"}])
    await asyncio.sleep(0)
    assert await worker.submit([{"type": "text-delta", "delta": "two"}])
    assert not await worker.submit([{"type": "text-delta", "delta": "overflow"}])
    assert failures == ["CHANNEL_QUEUE_OVERFLOW"]
    outbound.release.set()
    assert not await worker.finalize()


class _FailingOutbound:
    async def feed_events(self, events):
        raise RuntimeError("platform unavailable")

    async def finalize(self):
        raise AssertionError("failed delivery must not finalize as success")


@pytest.mark.asyncio
async def test_channel_send_failure_is_isolated_and_recorded() -> None:
    failures = []

    async def on_failure(code: str):
        failures.append(code)

    worker = ChannelDeliveryWorker(
        _FailingOutbound(),
        max_batches=4,
        max_bytes=4096,
        drain_seconds=0.1,
        on_failure=on_failure,
    )
    assert await worker.submit([{"type": "finish"}])
    assert not await worker.finalize()
    assert failures == ["CHANNEL_SEND_FAILED"]
