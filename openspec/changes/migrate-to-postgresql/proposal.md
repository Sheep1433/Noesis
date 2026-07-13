## Why

Noesis 以 PostgreSQL 作为唯一关系型数据库基线，使业务数据与 LangGraph checkpoint 可由统一的数据库运维、备份和权限体系管理，并支持多实例共享 checkpoint。

本变更不调整 `/api` 对外接口、SSE 帧契约、业务数据模型语义或 Qdrant 的向量检索职责；Qdrant 仍是唯一的向量数据库。

## What Changes

- PostgreSQL 是唯一支持的关系型数据库和运行配置来源。
- 新增统一的 PostgreSQL 连接配置，供 SQLAlchemy 异步应用连接、Alembic 迁移及 LangGraph checkpoint 使用。
- LangGraph checkpoint 使用 PostgreSQL 持久化，启动时自动初始化所需表。
- 将现有 Alembic 迁移、初始化脚本和演示数据改为 PostgreSQL 原生实现；不保留历史数据库迁移、兼容或回滚方案。
- 更新 Docker Compose、后端镜像、示例配置和部署文档，使 PostgreSQL 成为默认且受健康检查保护的关系型依赖。

## Capabilities

### New Capabilities

- `postgresql-persistence`: 规定 PostgreSQL 对业务关系数据与 LangGraph checkpoint 的连接、初始化、隔离和迁移验收行为。

### Modified Capabilities

- `container-deployment`: 部署配置提供 PostgreSQL 依赖与配置注入。
- `platform-chat`: 聊天会话和消息的服务端持久化后端改为 PostgreSQL，且既有 API 与 SSE 行为保持兼容。

## Impact

- 后端：`config/database.py`、`config/env.py`、`config/checkpointer.py`、Alembic 环境与版本迁移、数据库初始化脚本、依赖锁文件及相关测试。
- 部署：`deploy/docker-compose.yml`、后端 Dockerfile、Docker 环境变量与配置示例、部署和数据库操作文档。
- 数据：以 PostgreSQL 空库作为唯一启动基线；不支持导入历史数据库数据。
- 依赖：使用 PostgreSQL 异步/同步驱动与 LangGraph PostgreSQL checkpointer。
