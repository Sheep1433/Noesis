"""Leader elector：advisory lock 竞争 + 全局 leadership term。

enable-distributed-sse-pubsub 决策 1：执行锁 key 不变（滚动升级期间
新旧代码互斥，防双 leader）；获锁后在 t_runtime_leader 原子递增
leader_term。term 是全局任期而非按 Run lease——不迁移、不定时续约，
只在「失锁未感知」窗口用于拒绝旧 term 的 claim / checkpoint / 终态。

双角色（task 2.3/2.4）：``acquire`` 保持 fail-fast（memory 模式第二实例
必须启动失败）；``run_as_worker``（redis 模式）拿不到锁即以 follower 待命
并周期重竞选，晋升时回调 ``on_promotion``——recovery 四段对账与 singleton
runtime 的启动都挂在该回调上（进程启动只是晋升的首例）。
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid

from noesis.repositories.runtime_leader_repository import (
    RuntimeLeaderRepository,
)
from noesis.runtime.logging import logger
from noesis.storage.postgres.manager import pg_manager


class LeadershipLostError(RuntimeError):
    """leadership token 已失效（失锁/主动释放），拒绝以 leader 身份操作。"""


class LeadershipToken:
    """不可复用的 leadership 凭据：失锁后 invalidate，claim 前必须校验。"""

    def __init__(self, *, term: int, instance_id: str, cluster_id: str) -> None:
        self._term = term
        self._instance_id = instance_id
        self._cluster_id = cluster_id
        self._valid = True

    @property
    def term(self) -> int:
        return self._term

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def cluster_id(self) -> str:
        return self._cluster_id

    @property
    def valid(self) -> bool:
        return self._valid

    def require_valid(self) -> None:
        if not self._valid:
            raise LeadershipLostError(
                f"leadership 已失效 instance_id={self._instance_id} term={self._term}"
            )

    def _invalidate(self) -> None:
        self._valid = False


class LeaderElector:
    """包装 advisory lock 与 t_runtime_leader term 提交。"""

    def __init__(self, *, cluster_id: str) -> None:
        self.cluster_id = cluster_id
        self.instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._token: LeadershipToken | None = None

    @property
    def token(self) -> LeadershipToken | None:
        return self._token

    async def acquire(self) -> LeadershipToken:
        """竞争执行锁并提交新任期。第二个实例获取失败直接抛错（fail-fast）。"""
        await pg_manager.acquire_advisory_lock()
        async with pg_manager.get_async_session_context() as db:
            term = await RuntimeLeaderRepository(db).claim_leader_term(
                cluster_id=self.cluster_id,
                instance_id=self.instance_id,
                now_ms=int(time.time() * 1000),
            )
            await db.commit()
        token = LeadershipToken(
            term=term, instance_id=self.instance_id, cluster_id=self.cluster_id
        )
        self._token = token
        logger.info(
            "leader elector 已获锁并提交任期 cluster_id={} term={} instance_id={}",
            self.cluster_id,
            term,
            self.instance_id,
        )
        return token

    def invalidate(self) -> None:
        """advisory lock 丢失时由 lifespan monitor 调用：token 立即失效。"""
        if self._token is not None:
            self._token._invalidate()
            logger.error(
                "leader term 已失效（执行锁丢失）cluster_id={} instance_id={}",
                self.cluster_id,
                self.instance_id,
            )

    async def release(self) -> None:
        """释放执行锁（lifespan 关闭序列的最后一步：先 drain 后放锁）。"""
        self._stop_re_election()
        if self._token is not None:
            self._token._invalidate()
            self._token = None
        await pg_manager.release_advisory_lock()

    # ---- follower 候选循环（redis 模式，task 2.3/2.4） ------------------

    _RE_ELECTION_INTERVAL_SECONDS = 5.0
    _re_election_task: "asyncio.Task | None" = None

    @property
    def is_leader(self) -> bool:
        token = self._token
        return token is not None and token.valid

    async def run_as_worker(self, *, on_promotion) -> None:
        """redis 模式启动入口：竞争执行锁，未获即 follower 待命 + 周期重竞选。

        晋升（含启动时首获）调用 on_promotion(token)：recovery 对账、
        dispatcher/scheduler 等 leader-only runtime 的启动由回调完成；
        回调异常只记日志（本进程保持既有角色，下轮重试）。
        """
        if await pg_manager.try_advisory_lock():
            token = await self._claim_term()
            await self._invoke_promotion(on_promotion, token)
            return
        logger.info("execution leader 已由其它实例持有，本进程以 Web worker 待命")
        self._start_re_election(on_promotion)

    def _start_re_election(self, on_promotion) -> None:
        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._RE_ELECTION_INTERVAL_SECONDS)
                if not self.is_leader:
                    try:
                        if await pg_manager.try_advisory_lock():
                            token = await self._claim_term()
                            await self._invoke_promotion(on_promotion, token)
                            return
                    except Exception:  # noqa: BLE001
                        logger.warning("leader 重竞选轮询异常（下轮重试）")

        self._re_election_task = asyncio.get_running_loop().create_task(
            _loop(), name="leader-re-election"
        )

    def _stop_re_election(self) -> None:
        task = self._re_election_task
        self._re_election_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _claim_term(self) -> "LeadershipToken":
        async with pg_manager.get_async_session_context() as db:
            term = await RuntimeLeaderRepository(db).claim_leader_term(
                cluster_id=self.cluster_id,
                instance_id=self.instance_id,
                now_ms=int(time.time() * 1000),
            )
            await db.commit()
        token = LeadershipToken(
            term=term, instance_id=self.instance_id, cluster_id=self.cluster_id
        )
        self._token = token
        logger.info(
            "leader 晋升（重竞选成功）cluster_id={} term={} instance_id={}",
            self.cluster_id, term, self.instance_id,
        )
        return token

    @staticmethod
    async def _invoke_promotion(on_promotion, token: "LeadershipToken") -> None:
        try:
            await on_promotion(token)
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).error(
                "leader 晋升回调执行失败 term={}（本进程保持角色，等待下轮）",
                token.term,
            )
