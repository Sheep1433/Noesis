"""t_runtime_leader 的 SQLAlchemy repository（enable-distributed-sse-pubsub）。"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.runtime.logging import logger
from noesis.storage.postgres.models.runtime_leader import TRuntimeLeader


class ClusterIdMismatchError(RuntimeError):
    """t_runtime_leader 中存在其它 cluster_id 的行——集群标识配置错误，fail-fast。"""


class RuntimeLeaderRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def claim_leader_term(
        self, *, cluster_id: str, instance_id: str, now_ms: int
    ) -> int:
        """原子递增全局 leader term 并返回新任期。

        cluster_id 是集群身份防线：表内出现其它 cluster_id 的行说明
        NOESIS_CLUSTER_ID 变更后指向同一数据库（两套独立 term 序列会互相
        失去 fencing 作用），必须拒绝启动。
        """
        rows = (await self.db.execute(select(TRuntimeLeader.cluster_id))).scalars().all()
        foreign = [row for row in rows if row != cluster_id]
        if foreign:
            raise ClusterIdMismatchError(
                f"t_runtime_leader 存在其它 cluster_id={foreign}（当前配置 {cluster_id}）；"
                "请检查 NOESIS_CLUSTER_ID 是否在指向同一数据库的环境间被改动"
            )
        result = await self.db.execute(
            text(
                """
                INSERT INTO t_runtime_leader (cluster_id, leader_term, instance_id, updated_at)
                VALUES (:cluster_id, 1, :instance_id, :now_ms)
                ON CONFLICT (cluster_id) DO UPDATE SET
                    leader_term = t_runtime_leader.leader_term + 1,
                    instance_id = :instance_id,
                    updated_at = :now_ms
                RETURNING leader_term
                """
            ),
            {"cluster_id": cluster_id, "instance_id": instance_id, "now_ms": now_ms},
        )
        term = int(result.scalar_one())
        logger.info(
            "leader term 已提交 cluster_id={} leader_term={} instance_id={}",
            cluster_id,
            term,
            instance_id,
        )
        return term

    async def get_current_term(self, *, cluster_id: str) -> int | None:
        result = await self.db.execute(
            select(TRuntimeLeader.leader_term).where(
                TRuntimeLeader.cluster_id == cluster_id
            )
        )
        row = result.scalar_one_or_none()
        return int(row) if row is not None else None
