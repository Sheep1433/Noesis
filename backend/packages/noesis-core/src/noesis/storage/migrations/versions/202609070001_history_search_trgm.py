"""History search: pg_trgm GIN index + compaction boundary column (session-history-search).

Revision ID: 202609070001
Revises: 202608280002
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op

revision = "202609070001"
down_revision = "202608280002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pg_trgm 是检索的前提依赖：幂等创建；权限不足时明确失败（部署前置项，
    # 不静默跳过——跳过会让检索退化为全表扫描且无人知晓）。
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column(
        "t_chat_session",
        sa.Column(
            "compaction_cutoff_seq",
            sa.BigInteger(),
            nullable=True,
            comment="压缩遮蔽边界：最近一次压缩完成时已终态消息的最大序号；NULL=从未压缩；见 session-history-search",
        ),
    )
    # content 为 JSON 列，索引建在其文本序列化上；fastupdate=off 见
    # services/history_search.py 模块注释（写入侧调参依据）。幂等 + 收尾
    # ANALYZE 让规划器立刻可用 trigram 计划。
    # 非 CONCURRENTLY 的取舍：本仓迁移在应用启动期、migration lock 串行下
    # 执行（server/main.py），迁移完成前不对外服务；CONCURRENTLY 需脱离
    # Alembic 事务性 DDL（autocommit block），会破坏本迁移"扩展+列+索引"
    # 的单事务原子性。消息表出现生产级体量后再重估。
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_content_trgm "
        "ON t_chat_message USING gin ((content::text) gin_trgm_ops) "
        "WITH (fastupdate = off)"
    )
    op.execute("ANALYZE t_chat_message")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_message_content_trgm")
    op.drop_column("t_chat_session", "compaction_cutoff_seq")
