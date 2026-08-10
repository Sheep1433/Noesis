## ADDED Requirements

### Requirement: 知识库引擎与数据访问 SHALL 位于核心包

知识库 RAG 引擎 SHALL 位于 `packages/noesis-core/src/noesis/knowledge/`。集合配置的 ORM 与 repository SHALL 位于 `noesis.storage` 和 `noesis.repositories`。`backend/server` SHALL 仅保留 HTTP API 与启动组装，不得持有第二套知识库引擎、ORM、repository 或数据库连接。

#### Scenario: server 不持有知识库实现

- **WHEN** 扫描 `backend/server`
- **THEN** SHALL 不存在 `knowledge`、ORM、repository 或独立 engine 实现
- **AND** `server.api.knowledge_base_api` SHALL 调用 `noesis.services.knowledge_base_service`

#### Scenario: 核心包不反向依赖 server

- **WHEN** 扫描 `packages/noesis-core/src/noesis/knowledge` 与 `noesis.repositories`
- **THEN** SHALL 不存在 `server.*` import
- **AND** SHALL 不调用已删除的 KB runtime dependency binding

### Requirement: 集合配置 SHALL 经核心 repository 访问

集合级 `processing_params` / `query_params` SHALL 持久化在 PostgreSQL，并经 `noesis.repositories` 访问。请求级事务 SHALL 使用调用方注入的 `AsyncSession`；Agent 工具线程的同步读取 SHALL 使用 `pg_manager.get_sync_session()`。系统 SHALL NOT 为集合配置创建第二套 engine。

#### Scenario: Service 共享请求事务

- **WHEN** Knowledge 应用 service 在一次请求中读写集合和配置
- **THEN** SHALL 在同一请求级 session 中调用 repository
- **AND** commit/rollback SHALL 由应用 service 控制

#### Scenario: Agent 同步读取配置

- **WHEN** Agent 工具在线程内读取集合查询参数
- **THEN** SHALL 经 `noesis.repositories` 和 `pg_manager.get_sync_session()` 取得
- **AND** SHALL NOT 依赖 `server` 或自建 sync engine

## MODIFIED Requirements

### Requirement: Knowledge SHALL 使用 manager 管理资源生命周期

`noesis.knowledge.runtime.knowledge_base` SHALL 是进程级 `KnowledgeBaseManager`。manager SHALL 持有 Qdrant client 与连接状态，并通过 `KnowledgeBaseFactory` 创建绑定当前 client 的实现。应用 service 负责知识库用例和事务，API 负责 HTTP 翻译；Qdrant adapter SHALL NOT 保存模块级可变 client/connection 状态或兼容入口。

#### Scenario: 进程启动与关闭

- **WHEN** FastAPI lifespan 或评测运行时启动/结束
- **THEN** SHALL 调用 `noesis.knowledge.runtime` 的 initialize/close 入口
- **AND** 异常退出 SHALL 仍释放 Qdrant client

### Requirement: DeepDoc 为解析引擎

文档解析 SHALL 经 `noesis.knowledge.parser` 的 DeepDoc / `ParserFactory`；支持至少 pdf、docx、pptx、markdown、excel 等已实现格式。解析失败 SHALL 返回可定位错误，SHALL NOT 静默写入空向量。DeepDoc 与 `_ragflow_compat` 为 vendored 代码，内部 SHALL NOT import Noesis `server.*`。

#### Scenario: Markdown 解析

- **WHEN** 上传 `.md` 并触发解析
- **THEN** SHALL 产出可用于分块的非空文本

### Requirement: 分块与 hybrid 检索

系统 SHALL 经 `noesis.knowledge.chunking` 分块；`KbRetrievalService` SHALL 提供 hybrid 检索与可选 rerank。查询参数来自集合配置与请求覆盖。Agent 工具 SHALL 直接调用 `noesis.knowledge`，不得经 runtime dependency binding。

#### Scenario: 检索返回命中

- **WHEN** 集合已有向量且查询相关
- **THEN** SHALL 返回带分值、文档身份和引用定位信息的命中

#### Scenario: Qdrant 404 单独处理

- **WHEN** Qdrant 返回集合不存在
- **THEN** SHALL 映射为不存在语义
- **AND** SHALL NOT 被笼统捕获成未预期 500

### Requirement: 集合配置与 HTTP API

系统 SHALL 保持现有知识库 HTTP 路径和字段。`server.api.knowledge_base_api` SHALL 负责认证、参数和响应；`noesis.services.knowledge_base_service` SHALL 负责用例；`noesis.knowledge`、`noesis.repositories` 与 `noesis.storage` SHALL 提供实现和数据访问。

#### Scenario: 读写集合配置

- **WHEN** 客户端读取或更新已存在集合的配置
- **THEN** API SHALL 经应用 service 和 repository 完成
- **AND** 返回的 HTTP status 与业务 code SHALL 一致
