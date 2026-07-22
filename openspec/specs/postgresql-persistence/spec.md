# postgresql-persistence Specification

## Purpose
TBD - created by archiving change migrate-to-postgresql. Update Purpose after archive.
## Requirements
### Requirement: 业务关系数据 SHALL 使用 PostgreSQL

系统 SHALL 使用 PostgreSQL 作为用户、用户会话、聊天会话、聊天消息、聊天附件和知识库集合配置的唯一关系型持久化后端。应用运行时 SHALL 使用 SQLAlchemy 异步 PostgreSQL 引擎，Alembic 和需要同步会话的内部服务 SHALL 使用 PostgreSQL 同步连接。

#### Scenario: 应用启动连接业务库

- **WHEN** 后端以有效 PostgreSQL 业务库配置启动
- **THEN** 系统 SHALL 执行待应用的 Alembic 迁移并完成 `SELECT 1` 连接校验后进入可服务状态

#### Scenario: 缺少或无法连接业务 PostgreSQL

- **WHEN** PostgreSQL 业务库配置缺失、凭证无效或网络不可达
- **THEN** 后端 SHALL 启动失败并记录可定位的连接错误

### Requirement: LangGraph checkpoint SHALL 使用独立 PostgreSQL 数据库

系统 SHALL 使用 PostgreSQL async checkpointer 保存 LangGraph checkpoint，并在应用 lifespan 启动时初始化其表、关闭时释放连接池。checkpoint 数据库 SHALL 与业务数据库逻辑隔离，且 checkpoint 初始化或清理 SHALL NOT 修改业务表。

#### Scenario: Agent 写入并恢复 checkpoint

- **WHEN** 一个启用 checkpointer 的 Agent 为同一 thread 执行多步或恢复执行
- **THEN** checkpoint SHALL 写入并从配置的 PostgreSQL checkpoint 数据库读取，行为与既有 Agent 编排契约一致

#### Scenario: 多应用实例共享 checkpoint

- **WHEN** 两个后端实例配置为同一 PostgreSQL checkpoint 数据库且使用相同 thread 标识
- **THEN** 任一实例 SHALL 能读取另一个实例已提交的 checkpoint

### Requirement: PostgreSQL 配置 SHALL 是唯一权威连接源

系统 SHALL 通过 `POSTGRES_*` 业务库配置和专用 LangGraph PostgreSQL 配置构造连接 URL；示例配置、容器环境、启动日志和部署文档 SHALL 使用相同键名。

#### Scenario: 配置键一致启动

- **WHEN** 运维仅提供文档规定的 PostgreSQL 配置键和有效凭证
- **THEN** 后端、Alembic 初始化工具及 LangGraph checkpointer SHALL 连接到各自约定的 PostgreSQL 数据库

