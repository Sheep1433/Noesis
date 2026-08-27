"""Leader elector：advisory lock 竞争 + 全局 leadership term（P1）。

enable-distributed-sse-pubsub 决策 1：执行锁 key 不变（滚动升级期间
新旧代码互斥，防双 leader）；获锁后在 t_runtime_leader 原子递增
leader_term。term 是全局任期而非按 Run lease——不迁移、不定时续约，
只在「失锁未感知」窗口用于拒绝旧 term 的 claim / checkpoint / 终态。

P1 仅 memory 单进程：未获锁直接 fail-fast（follower 角色在 P3 接入）。
"""

from __future__ import annotations

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
        if self._token is not None:
            self._token._invalidate()
            self._token = None
        await pg_manager.release_advisory_lock()
