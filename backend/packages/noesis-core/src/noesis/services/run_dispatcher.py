"""Run dispatcher：leader 消费 run-created 唤醒并 claim/启动 queued Run。

enable-distributed-sse-pubsub 决策 2：任意 worker 的 create 只写
``queued + owner IS NULL + launch_payload``；本组件只在 leader 进程
运行——bus wake-up 即时检查 + 周期补扫（唤醒丢失兜底）。

claim 条件：queued 且未被认领；容量满则跳过等下轮（保持 queued，
不收口 error）。claim 成功但启动失败必须收口（RUN_START_FAILED），
不留无 producer 的 running 行。
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from noesis.chat.runs import RunCapacityExceeded
from noesis.chat.runs.bus import (
    RunBus,
    WAKEUP_TOPIC_RUN_CREATED,
)
from noesis.chat.runs.launch_payload import LaunchPayload
from noesis.runtime.logging import logger
from noesis.repositories.agent_run_repository import AgentRunRepository
from noesis.services.leader_elector import LeadershipToken
from noesis.services.run_service import RunService, run_manager
from noesis.services.user_service import UserService
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.chat import TAgentRun


def _now_ms() -> int:
    return int(time.time() * 1000)


class RunDispatcher:
    """单消费者协程：串行 claim，避免同一批 queued Run 的并发容量误判。"""

    def __init__(
        self,
        *,
        bus: RunBus,
        token_provider: Callable[[], LeadershipToken | None],
        scan_interval_seconds: float,
    ) -> None:
        self._bus = bus
        self._token_provider = token_provider
        self._scan_interval = scan_interval_seconds
        self._task: asyncio.Task | None = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        # wakeup 订阅先建（subscribe-first），再起消费循环，防丢唤醒
        wakeup_sub = self._bus.subscribe_wakeups()
        self._task = asyncio.create_task(
            self._run(wakeup_sub), name="run-dispatcher"
        )

    async def stop(self) -> None:
        """停止 claim 并等待在途启动完成（优雅关闭序列中位于 producer drain 之后）。"""
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self, wakeup_sub) -> None:
        try:
            await wakeup_sub.ready()
            while not self._stopping:
                # 等唤醒（超时即周期补扫）；唤醒后短暂去抖合并突发创建
                try:
                    await asyncio.wait_for(
                        wakeup_sub.__aiter__().__anext__(),
                        timeout=self._scan_interval,
                    )
                except asyncio.TimeoutError:
                    pass
                except StopAsyncIteration:
                    # bus 关闭：退出前做最后一次补扫
                    await self._scan_once()
                    return
                await self._drain_pending_wakeups(wakeup_sub)
                await self._scan_once()
        finally:
            await wakeup_sub.close()

    async def _drain_pending_wakeups(self, wakeup_sub) -> None:
        """合并 100ms 内到达的唤醒，一次补扫处理整批。"""
        try:
            await asyncio.wait_for(
                wakeup_sub.__aiter__().__anext__(), timeout=0.1
            )
        except (asyncio.TimeoutError, StopAsyncIteration):
            return

    async def _scan_once(self) -> None:
        token = self._token_provider()
        if token is None or not token.valid:
            return
        try:
            async with pg_manager.get_async_session_context() as db:
                repository = AgentRunRepository(db)
                queued = await repository.list_claimable_queued(limit=20)
                for row in queued:
                    if self._stopping:
                        return
                    await self._claim_and_start(repository, row, token, db)
        except Exception:
            logger.exception("run dispatcher scan failed")

    async def _claim_and_start(
        self,
        repository: AgentRunRepository,
        row: TAgentRun,
        token: LeadershipToken,
        db,
    ) -> None:
        token.require_valid()
        run_id = row.id
        # 容量预检在 claim 前：满则保持 queued 等下轮，不收口 error
        try:
            await run_manager.check_run_capacity(str(row.user_id))
        except RunCapacityExceeded:
            logger.info(
                "dispatcher 容量已满，run 保持 queued run_id={} user_id={}",
                run_id,
                row.user_id,
            )
            return
        claimed = await repository.claim_queued(
            run_id=run_id,
            owner_instance_id=token.instance_id,
            owner_term=token.term,
            now_ms=_now_ms(),
        )
        if not claimed:
            return  # 并发 claim 输家：下轮自然跳过
        # claim 先提交：启动路径（CAS queued→running）不再与本事务的行锁互等
        await db.commit()
        logger.info(
            "dispatcher 已 claim run run_id={} owner_term={} instance_id={}",
            run_id,
            token.term,
            token.instance_id,
        )
        await self._start_claimed_run(run_id)

    async def _start_claimed_run(self, run_id: str) -> None:
        # claim 已提交；以新 session 读权威行重建启动上下文
        async with pg_manager.get_async_session_context() as fresh_db:
            repository = AgentRunRepository(fresh_db)
            run = await repository.get(run_id)
            if run is None or run.status != "queued":
                return
            try:
                payload = LaunchPayload.from_dict(run.launch_payload or {})
                current_user = await UserService.get_user_by_id(
                    str(run.user_id), fresh_db
                )
            except Exception:
                # payload 损坏或用户已删除/禁用：claim 已提交，必须收口不留僵尸
                logger.exception(
                    "dispatcher 启动前上下文重建失败 run_id={}", run_id
                )
                await RunService._finalize_start_failure(run)
                return
        try:
            await RunService.start_queued_run(run, payload, current_user)
        except Exception:
            logger.exception("dispatcher 启动 run 失败 run_id={}", run_id)
            try:
                await RunService._finalize_start_failure(run)
            except Exception:
                logger.exception(
                    "dispatcher 启动失败收口失败 run_id={}", run_id
                )


__all__ = ["RunDispatcher"]
