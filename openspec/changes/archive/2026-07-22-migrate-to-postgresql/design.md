## Context

Noesis 的业务关系数据和 LangGraph checkpoint 均以 PostgreSQL 为唯一持久化基线。Qdrant 继续只负责向量和分片，附件正文继续保存于文件系统。

## Goals / Non-Goals

**Goals:**

- 使用 PostgreSQL 提供业务数据和共享 checkpoint。
- 让本地与 Compose 环境使用相同的 PostgreSQL 配置契约。
- 保持 REST API、SSE 事件和 Agent 编排语义不变。

**Non-Goals:**

- 不支持其它关系型数据库。
- 不提供历史数据导入、迁移或回滚工具。
- 不将 Qdrant 或附件文件迁入 PostgreSQL。

## Decisions

业务表位于 `noesis` 数据库，LangGraph checkpoint 位于独立的 `noesis_langgraph` 数据库。业务应用使用 SQLAlchemy `asyncpg`，同步迁移与工具使用 `psycopg`，checkpoint 使用 LangGraph PostgreSQL async saver 和连接池。Compose 创建持久化 PostgreSQL 服务及两个数据库。

## Risks / Trade-offs

- [数据库不可用阻止应用启动] → Compose 健康检查和应用启动连接校验。
- [checkpoint 增长影响备份] → 使用独立数据库并由运维设置保留策略。
