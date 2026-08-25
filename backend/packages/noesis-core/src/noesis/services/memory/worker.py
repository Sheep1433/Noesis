"""Single background worker for the staged machine-memory pipeline."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import MachineMemoryConfig
from noesis.repositories.machine_memory_repository import MachineMemoryRepository
from noesis.runtime.logging import logger
from noesis.services.memory.chunking import MemoryChunker
from noesis.services.memory.extractor import MemoryExtractor
from noesis.services.memory.model import StructuredCandidateModel
from noesis.services.memory.pipeline import MemoryPipelineProcessor
from noesis.services.memory.workspace import MemoryWorkspaceService
from noesis.services.memory.index import MemoryIndexService
from noesis.storage.postgres.manager import pg_manager


_task: asyncio.Task | None = None
_stop = asyncio.Event()


def _processor() -> MemoryPipelineProcessor:
    return MemoryPipelineProcessor(
        MemoryExtractor(
            StructuredCandidateModel(),
            concurrency=MachineMemoryConfig.chunk_concurrency,
            chunk_attempts=MachineMemoryConfig.chunk_attempts,
            retry_delay_seconds=MachineMemoryConfig.chunk_retry_delay_seconds,
        ),
        chunker=MemoryChunker(max_tokens=MachineMemoryConfig.chunk_max_tokens),
        retry_seconds=MachineMemoryConfig.retry_seconds,
    )


async def _claim(worker_id: str) -> list[tuple[str, str]]:
    async with pg_manager.get_async_session_context() as db:
        repository = MachineMemoryRepository(db)
        await repository.reap_exhausted_jobs()
        jobs = await repository.claim_jobs(
            worker_id=worker_id,
            limit=MachineMemoryConfig.claim_batch_size,
            lease_seconds=MachineMemoryConfig.lease_seconds,
        )
        claimed = [(job.id, str(job.claim_token)) for job in jobs if job.claim_token]
        await db.commit()
        return claimed


async def _run_lease_heartbeat(
    *, stop: asyncio.Event, renew: Callable[[AsyncSession], Awaitable[bool]]
) -> None:
    """Renew a claim lease until stopped or the claim is lost."""
    interval = max(1.0, MachineMemoryConfig.lease_seconds / 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        async with pg_manager.get_async_session_context() as db:
            renewed = await renew(db)
            await db.commit()
            if not renewed:
                return


def _start_lease_heartbeat(
    *,
    name: str,
    stop: asyncio.Event,
    renew: Callable[[AsyncSession], Awaitable[bool]],
) -> asyncio.Task:
    return asyncio.create_task(_run_lease_heartbeat(stop=stop, renew=renew), name=name)

async def _renew_job(db: AsyncSession, *, job_id: str, claim_token: str) -> bool:
    return await MachineMemoryRepository(db).renew_job(
        job_id=job_id,
        claim_token=claim_token,
        lease_seconds=MachineMemoryConfig.lease_seconds,
    )


async def _process(processor: MemoryPipelineProcessor, job_id: str, claim_token: str) -> None:
    heartbeat_stop = asyncio.Event()
    heartbeat_task = _start_lease_heartbeat(
        name=f"machine-memory-heartbeat:{job_id}",
        stop=heartbeat_stop,
        renew=lambda db: _renew_job(db, job_id=job_id, claim_token=claim_token),
    )
    try:
        async with pg_manager.get_async_session_context() as db:
            outcome = await asyncio.wait_for(
                processor.process(db, job_id=job_id, claim_token=claim_token),
                timeout=MachineMemoryConfig.stage_timeout_seconds,
            )
            logger.info("machine memory job stage finished job_id={} outcome={}", job_id, outcome)
    except TimeoutError:
        async with pg_manager.get_async_session_context() as db:
            repository = MachineMemoryRepository(db)
            job = await repository.get_claimed_job(
                job_id=job_id, claim_token=claim_token
            )
            if job is not None:
                await repository.retry_claimed_job(
                    job_id=job_id,
                    claim_token=claim_token,
                    status="failed",
                    stage_result=job.stage_result or {},
                    coverage=job.coverage or {},
                    error_summary="stage timeout",
                    retry_seconds=MachineMemoryConfig.retry_seconds,
                )
            await db.commit()
    finally:
        heartbeat_stop.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _claim_outbox(worker_id: str) -> list[tuple[str, str, str, str, str, str | None]]:
    async with pg_manager.get_async_session_context() as db:
        repository = MachineMemoryRepository(db)
        await repository.reap_exhausted_outbox()
        events = await repository.claim_outbox(
            worker_id=worker_id,
            limit=MachineMemoryConfig.claim_batch_size,
            lease_seconds=MachineMemoryConfig.lease_seconds,
        )
        claimed = [
            (
                event.id,
                str(event.claim_token),
                event.target,
                str(event.user_id),
                event.scope_key,
                event.memory_id,
            )
            for event in events
            if event.claim_token
        ]
        await db.commit()
        return claimed


async def _process_outbox_once(
    event: tuple[str, str, str, str, str, str | None]
) -> None:
    event_id, claim_token, target, user_id, scope_key, memory_id = event
    async with pg_manager.get_async_session_context() as db:
        repository = MachineMemoryRepository(db)
        try:
            if target == "workspace":
                await MemoryWorkspaceService.rebuild(
                    db, user_id=user_id, scope_key=scope_key
                )
            elif target == "index" and memory_id:
                await MemoryIndexService().sync_item(
                    db,
                    user_id=user_id,
                    scope_key=scope_key,
                    memory_id=memory_id,
                )
            elif target == "index":
                await MemoryIndexService().rebuild(db)
            else:
                raise ValueError(f"unsupported memory outbox target: {target}")
        except Exception as exc:
            await db.rollback()
            await repository.retry_outbox(
                event_id=event_id,
                claim_token=claim_token,
                error_summary=f"{type(exc).__name__}: {exc}",
                retry_seconds=MachineMemoryConfig.retry_seconds,
            )
            await db.commit()
            logger.warning("machine memory derived view sync failed event_id={}", event_id)
            return
        if not await repository.finish_outbox(
            event_id=event_id, claim_token=claim_token
        ):
            await db.rollback()
            return
        await db.commit()


async def _process_outbox(
    event: tuple[str, str, str, str, str, str | None]
) -> None:
    event_id, claim_token, *_ = event
    heartbeat_stop = asyncio.Event()

    async def renew(db: AsyncSession) -> bool:
        return await MachineMemoryRepository(db).renew_outbox(
            event_id=event_id,
            claim_token=claim_token,
            lease_seconds=MachineMemoryConfig.lease_seconds,
        )

    heartbeat_task = _start_lease_heartbeat(
        name=f"machine-memory-outbox-heartbeat:{event_id}",
        stop=heartbeat_stop,
        renew=renew,
    )
    try:
        await asyncio.wait_for(
            _process_outbox_once(event),
            timeout=MachineMemoryConfig.stage_timeout_seconds,
        )
    except TimeoutError:
        async with pg_manager.get_async_session_context() as db:
            await MachineMemoryRepository(db).retry_outbox(
                event_id=event_id,
                claim_token=claim_token,
                error_summary="derived view update timeout",
                retry_seconds=MachineMemoryConfig.retry_seconds,
            )
            await db.commit()
    finally:
        heartbeat_stop.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _cleanup() -> None:
    now = datetime.now(timezone.utc)
    snapshot_before = now - timedelta(days=MachineMemoryConfig.snapshot_retention_days)
    async with pg_manager.get_async_session_context() as db:
        repository = MachineMemoryRepository(db)
        expired_scopes = await repository.expired_snapshot_scopes(before=snapshot_before)
        for user_id, scope_key in expired_scopes:
            repository.add_scope_rebuild_event(
                user_id=user_id, scope_key=scope_key
            )
        snapshots = await repository.cleanup_snapshots(before=snapshot_before)
        jobs = await repository.cleanup_terminal_jobs(
            before=now - timedelta(days=MachineMemoryConfig.job_retention_days)
        )
        outbox = await repository.cleanup_terminal_outbox(
            before=now - timedelta(days=MachineMemoryConfig.job_retention_days)
        )
        traces = await repository.cleanup_query_traces(before=snapshot_before)
        await db.commit()
        if snapshots or jobs or outbox or traces:
            logger.info(
                "machine memory retention cleanup snapshots={} jobs={} outbox={} traces={}",
                snapshots,
                jobs,
                outbox,
                traces,
            )


async def _loop() -> None:
    worker_id = f"{socket.gethostname()}:{id(asyncio.current_task())}"
    processor: MemoryPipelineProcessor | None = None
    next_cleanup = datetime.now(timezone.utc)
    logger.info("machine memory worker started")
    while not _stop.is_set():
        try:
            if processor is None:
                processor = _processor()
            for job_id, claim_token in await _claim(worker_id):
                if _stop.is_set():
                    break
                await _process(processor, job_id, claim_token)
            for event in await _claim_outbox(worker_id):
                if _stop.is_set():
                    break
                await _process_outbox(event)
            now = datetime.now(timezone.utc)
            if now >= next_cleanup:
                await _cleanup()
                next_cleanup = now + timedelta(hours=1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("machine memory worker tick failed")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=MachineMemoryConfig.poll_seconds)
        except TimeoutError:
            pass


def start_machine_memory_worker() -> None:
    global _task
    if _task is None or _task.done():
        _stop.clear()
        _task = asyncio.create_task(_loop(), name="machine-memory-worker")


async def stop_machine_memory_worker() -> None:
    global _task
    _stop.set()
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


__all__ = ["start_machine_memory_worker", "stop_machine_memory_worker"]
