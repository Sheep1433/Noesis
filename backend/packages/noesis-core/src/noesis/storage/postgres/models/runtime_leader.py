"""t_runtime_leader：全局 leadership term（enable-distributed-sse-pubsub）。"""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from noesis.storage.postgres.base import Base


class TRuntimeLeader(Base):
    """单行全局 leader term：每次获锁原子 +1，cluster_id 不一致 fail-fast。"""

    __tablename__ = "t_runtime_leader"
    __table_args__ = (
        Index("idx_runtime_leader_updated", "updated_at"),
        {"comment": "分布式 Run 协调的 leadership 任期"},
    )

    cluster_id: Mapped[str] = mapped_column(
        String(100), primary_key=True, comment="集群标识（redis 模式必填；memory 固定 local）"
    )
    leader_term: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="全局递增任期，每次 leader 获锁 +1"
    )
    instance_id: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="当前 leader 实例标识"
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), comment="term 提交时间戳（秒）"
    )
