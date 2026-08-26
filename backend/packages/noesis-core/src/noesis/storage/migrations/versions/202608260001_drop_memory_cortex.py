"""Drop machine memory cortex tables (replaced by md-file memory layer).

Revision ID: 202608260001
Revises: 202608250003
Create Date: 2026-08-26

按 openspec 变更 md-memory-layer：记忆唯一真相改为 md 文件（索引 + 分类条目
+ 情景日志），机器记忆皮层的八张表整体删除；t_agent_run.memory_context 字段
保留（复用为注入清单）。
"""

from alembic import op

revision = "202608260001"
down_revision = "202608250003"
branch_labels = None
depends_on = None

# 依赖顺序倒排：先删引用方（evidence/relation/job/outbox/trace），再删被引用方。
_TABLES = (
    "t_memory_query_trace",
    "t_memory_outbox",
    "t_memory_job",
    "t_memory_relation",
    "t_memory_evidence",
    "t_memory_run_snapshot",
    "t_memory_item",
    "t_memory_user_preference",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def downgrade() -> None:
    # 旧链路已删除，不提供回滚重建（数据经 0.2 导出任务迁移为文件条目）。
    raise NotImplementedError("memory cortex tables are not restorable")
