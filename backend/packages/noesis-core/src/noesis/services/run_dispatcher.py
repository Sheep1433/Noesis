"""Run dispatcher：worker 消费 run-created 唤醒并 claim/启动 queued Run。

enable-distributed-sse-pubsub 决策 2：任意 worker 的 create 只写
``queued + owner IS NULL + launch_payload``；worker-role-split 后本组件
在**每个 worker 进程**运行——bus wake-up 即时检查 + 周期补扫（唤醒丢失
兜底）。多 worker 并发认领由 CAS 裁决（输家跳过），圈行经
``FOR UPDATE SKIP LOCKED`` 减少同批竞争空转（正确性不依赖它）。

claim 条件：queued 且未被认领；容量满则跳过等下轮（保持 queued，
不标记 error）。claim 成功但启动失败必须标记终态（RUN_START_FAILED），
不留无 producer 的 running 行。持有期间的心跳与失去持有的自停由
RunService 的心跳协程负责（claim_epoch fencing）。
"""

from __future__ import annotations

import asyncio

from noesis.ids import now_ms
from noesis.chat.runs.bus import (
    RunBus,
)
from noesis.chat.runs.launch_payload import LaunchPayload
from noesis.runtime.logging import logger
from noesis.repositories.agent_run_repository import AgentRunRepository
from noesis.services.run_service import RunService, run_manager
from noesis.services.user_service import UserService
from noesis.storage.postgres.manager import pg_manager


class RunDispatcher:
    """worker 认领协程：圈行（SKIP LOCKED）+ 逐行 CAS 认领并启动。

    进程内串行（每 worker 一个），容量判定基于本进程 run_manager——
    多 worker 部署下容量天然 per-process。
    """

    def __init__(
        self,
        *,
        bus: RunBus,
        instance_id: str,
        scan_interval_seconds: float,
    ) -> None:
        self._bus = bus
        self._instance_id = instance_id
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
        try:
            async with pg_manager.get_async_session_context() as db:
                repository = AgentRunRepository(db)
                # 容量检查经回调注入，在圈行事务的行锁内逐行判定：
                # 本进程满（全局或该用户）的行不认领、保持 queued，
                # 锁释放后其他 worker / 下轮补扫仍可认领
                claimed = await repository.claim_next_batch(
                    owner_instance_id=self._instance_id,
                    limit=20,
                    now_ms=now_ms(),
                    capacity_check=run_manager.check_run_capacity,
                )
                await db.commit()
            # 圈行事务已提交（SKIP LOCKED + 同事务 CAS）；启动在事务外逐行进行
            for run_id, claim_epoch in claimed:
                if self._stopping:
                    return
                logger.info(
                    "dispatcher 已 claim run run_id={} epoch={} instance_id={}",
                    run_id,
                    claim_epoch,
                    self._instance_id,
                )
                await self._start_claimed_run(run_id, claim_epoch)
        except Exception:
            logger.exception("run dispatcher scan failed")

    async def _start_claimed_run(self, run_id: str, claim_epoch: int) -> None:
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
                # payload 损坏或用户已删除/禁用：claim 已提交，必须标记终态不留僵尸
                logger.exception(
                    "dispatcher 启动前上下文重建失败 run_id={}", run_id
                )
                await RunService._finalize_start_failure(run)
                return
        try:
            await RunService.start_queued_run(
                run,
                payload,
                current_user,
                owner_instance_id=self._instance_id,
                claim_epoch=claim_epoch,
            )
        except Exception:
            logger.exception("dispatcher 启动 run 失败 run_id={}", run_id)
            try:
                await RunService._finalize_start_failure(run)
            except Exception:
                logger.exception(
                    "dispatcher 启动失败终态处理失败 run_id={}", run_id
                )


__all__ = ["RunDispatcher"]
