## MODIFIED Requirements

### Requirement: Harness 为独立 Agent 内核包

系统 SHALL 将 Agent 工厂、LLM、agents、runtime、backends、middlewares、tools、prompts、mcp、skills、guardrails 置于 backend workspace distribution `noesis-harness`。distribution 目录 SHALL 为 `packages/harness`，其唯一 Python 顶层包 SHALL 为 `noesis`。harness SHALL 同时拥有全量数据持久层（`noesis.storage`：全量 ORM + DB engine + Alembic 迁移）、全量 repository（`noesis.repositories`）与知识库引擎（`noesis.knowledge`）。平台 Delivery、HTTP 编排、渠道适配与调度入口 **SHALL NOT** 位于该包内。

#### Scenario: 评测可 import noesis

- **WHEN** 离线评测进程启动
- **THEN** SHALL 能 `from noesis.factory import create_noesis_agent`，且 **SHALL NOT** 需要启动 FastAPI 或预先绑定平台 deps 才能加载工厂

#### Scenario: LLM、Agent 内核与数据层同包

- **WHEN** Agent、评测或平台 service 加载模型工厂、repository 或 DB engine
- **THEN** SHALL 从 `noesis.llm` / `noesis.repositories` / `noesis.storage` 导入
- **AND** SHALL NOT 存在平级 `packages/llm` distribution 或平台 `infrastructure/database` engine

### Requirement: 禁止 harness 反向依赖平台

`noesis` 包源码 **SHALL NOT** 静态 import 上层平台包：`noesis_server.*`，也 SHALL NOT 依赖外层 `config` / `common`。知识库检索引擎、全量数据访问层（DB engine、ORM、repository、迁移）SHALL 由 harness 内的 `noesis.knowledge` / `noesis.storage` / `noesis.repositories` 子系统直接提供，harness 工具、Agent、评测与平台 HTTP router SHALL 直接 import 这些子系统，SHALL NOT 经依赖注入绕行取得检索门面、Qdrant 客户端、集合配置、执行参数或 DB session。

知识库引擎需要 PostgreSQL 集合配置时，SHALL 经 `noesis.repositories` 直接读取；DB engine SHALL 由 `noesis.storage.postgres.manager`（`pg_manager` 单例）从 `noesis.config.env.DataBaseConfig` 构建。SHALL NOT 保留任何 KB 相关的 `bind_kb_*` / `require_kb_*` / `temporary_kb_runtime` 依赖注入面。Agent 运行时配置与日志 SHALL 由 `noesis.config` / `noesis.runtime.logging` 提供。

#### Scenario: 静态依赖检查

- **WHEN** 审查 `packages/harness/noesis/**/*.py`
- **THEN** AST 边界检查 SHALL 不存在对 `noesis_server` / 外层 `config` / 外层 `common` / `services` / `domain` / `api` 的静态 import
- **AND** `noesis.storage/**` / `noesis.repositories/**` / `noesis.knowledge/**` SHALL NOT 静态 import `noesis_server.*` / `services` / `models` / `domain` / `api`

#### Scenario: KB 检索不经注入绕行

- **WHEN** harness 工具 `kb_search_tool` 需要检索门面、Qdrant 客户端、集合配置或执行参数归一
- **THEN** SHALL 直接 import `noesis.knowledge` 与 `noesis.repositories`
- **AND** SHALL NOT 调用 `require_kb_retrieval_service` / `require_qdrant_service` / `require_normalize_query_execution_params` / `require_is_qdrant_connected` / `require_kb_collection_config_service`
- **AND** `noesis.runtime.deps` SHALL NOT 导出任何 KB 相关绑定函数

#### Scenario: DB engine 唯一来源为 harness storage

- **WHEN** 平台 service 或 harness knowledge 需要数据库 engine
- **THEN** SHALL 经 `noesis.storage.postgres.manager.pg_manager` 取得
- **AND** 平台 SHALL NOT 存在 `noesis_server.infrastructure.database.engine` 或独立 ORM 目录

#### Scenario: wheel 隔离导入

- **WHEN** 将构建出的 `noesis-harness` wheel 安装到 backend 源码目录外的新环境
- **THEN** SHALL 能导入 `noesis.factory`、`noesis.llm`、`noesis.knowledge`、`noesis.storage`、`noesis.repositories` 公共门面
- **AND** 不依赖 backend 源码出现在 `PYTHONPATH`
- **AND** 重型 DeepDoc 解析依赖（ONNX / PaddleOCR）不在门面导入时触发

### Requirement: 平台宿主使用单一 Python 命名空间

Web API、渠道适配、调度入口、平台 bootstrap 与平台薄编排 service SHALL 位于 `backend/noesis_server`，并使用 `noesis_server.*` 导入。backend 根目录 SHALL NOT 并列保留旧顶层 Python package 或兼容 shim。平台 SHALL NOT 拥有 DB engine、ORM model、repository 或 Alembic 迁移实现——这些 SHALL 位于 `noesis.storage` / `noesis.repositories`；平台 service 经 `noesis.repositories` 做 DB 访问，SHALL NOT 内联 ORM 或 engine。`evals`、`packages/harness`、Alembic CLI 与启动脚本不属于平台 package，保持独立。

#### Scenario: backend 顶层目录扫描

- **WHEN** 扫描 backend 根目录
- **THEN** SHALL 不存在顶层 `api` / `services` / `domain` / `config` / `common` / `constants` / `exceptions` / `middleware` / `models` / `schemas` / `kb` Python package
- **AND** `noesis_server/` SHALL NOT 包含 `infrastructure/database/` 或 `models/`

#### Scenario: 平台 service 经 repository 访问 DB

- **WHEN** 平台 service 读取或写入业务数据
- **THEN** SHALL 调用 `noesis.repositories` 的 repository
- **AND** SHALL NOT 直接操作 ORM model 或 DB engine

## ADDED Requirements

### Requirement: Harness SHALL 拥有全量数据持久层

系统 SHALL 在 harness 内提供 `noesis.storage` 与 `noesis.repositories` 子系统，覆盖全部业务域与知识库域的数据访问。`noesis.storage.postgres.manager` SHALL 以单例 `pg_manager` 拥有 async engine、sync engine 与 session factory，从 `noesis.config.env.DataBaseConfig` 构造，SHALL NOT 以模块级全局变量管理；`pg_manager.initialize()` SHALL 执行 Alembic 迁移（含 legacy stamp 逻辑）与连接校验，替代平台 `init_database`。`noesis.storage.postgres.base` SHALL 定义唯一 `Base`，全量 ORM model（认证、聊天/Run、调度、设置、知识库等全部表）继承该 `Base` 并位于 `noesis.storage.postgres.models`，SHALL NOT 位于平台 `noesis_server.models`。Alembic（`alembic.ini` + `env.py` + `versions/`）SHALL 位于 `noesis.storage.migrations`，SHALL NOT 位于 backend 根或平台 `infrastructure/database`。`noesis.repositories` SHALL 提供全量域 repository（构造注入 `__init__(self, db)`，session 由 `pg_manager.get_async_session_context()` 提供），SHALL NOT 依赖平台 service；平台 `get_db()` SHALL 委托 `pg_manager`，`Depends(get_db)` 签名保持不变。`noesis.config.checkpointer`（LangGraph checkpoint，独立 checkpoint 库，psycopg 原生连接池）SHALL 与 `pg_manager`（业务库 SQLAlchemy engine）并存，SHALL NOT 合并。

#### Scenario: engine 与 ORM 由 harness 单例拥有

- **WHEN** 审查 `noesis.storage.postgres`
- **THEN** SHALL 存在 `PostgresManager` 单例 `pg_manager`
- **AND** 全量 ORM SHALL 位于 `noesis.storage.postgres.models`
- **AND** SHALL NOT 存在模块级可变 engine 全局
- **AND** engine 构造 SHALL 仅依赖 `noesis.config.env.DataBaseConfig`

#### Scenario: 迁移归属 harness

- **WHEN** 运维执行 Alembic 迁移
- **THEN** SHALL 从 `noesis.storage.migrations` 运行
- **AND** `env.py` SHALL 指向 `noesis.storage.postgres.models` metadata
- **AND** 平台 SHALL NOT 存在独立迁移目录

#### Scenario: repository 经 harness session

- **WHEN** 任意域 repository 读取数据
- **THEN** 其 session SHALL 由 `pg_manager.get_async_session_context()` 提供（构造注入 `__init__(self, db)`）
- **AND** 平台 `get_db()` SHALL 委托 `pg_manager`，`Depends(get_db)` 签名不变
- **AND** SHALL NOT import 平台 `infrastructure.database` 或 `noesis_server.services`

#### Scenario: 单一 Base 与全量 ORM 注册

- **WHEN** 审查 `noesis.storage.postgres`
- **THEN** SHALL 存在唯一定义的 `Base`（`noesis.storage.postgres.base`），全量 ORM model 继承同一 `Base`
- **AND** `models/__init__.py` SHALL 注册全部 model 确保 `Base.metadata` 完整

### Requirement: Knowledge 引擎 SHALL 以抽象基类与工厂装配

`noesis.knowledge` SHALL 定义 `KnowledgeBase` 抽象基类，声明检索、整篇拉取、集合信息与入库等当前真实被调用的统一接口；`KnowledgeBaseFactory` SHALL 维护 `{kb_type: 实现}` 注册表并在运行时装配。Qdrant 实现 SHALL 位于 `noesis.knowledge.implementations.qdrant` 并实现该 ABC。`KnowledgeBaseManager` SHALL 经 `noesis.repositories` 读集合配置、own Qdrant 客户端生命周期，并暴露 create/upload/search/delete 等领域方法，抛 domain exception。系统 SHALL NOT 在本变更落地第二套检索后端实现，SHALL NOT 在 ABC 上预先声明无调用方的方法。

#### Scenario: Qdrant 实现经工厂注册

- **WHEN** harness 运行时装配点初始化知识库子系统
- **THEN** `KnowledgeBaseFactory` SHALL 已注册 Qdrant 实现
- **AND** 经工厂取得的实例 SHALL 为 `KnowledgeBase` 子类

#### Scenario: manager 直连 repository

- **WHEN** `KnowledgeBaseManager` 读取或更新集合配置
- **THEN** SHALL 调用 `noesis.repositories` 的 repository
- **AND** SHALL NOT 经依赖注入端口取得配置

#### Scenario: 仅一套实现

- **WHEN** 审查 `noesis.knowledge.implementations`
- **THEN** SHALL 只存在 Qdrant 一个具体实现
- **AND** SHALL NOT 存在并行运行的第二套向量库后端或兼容 stub

### Requirement: Knowledge 客户端生命周期 SHALL 由管理器拥有

Qdrant 客户端实例与连接状态 SHALL 由 `noesis.knowledge.manager.KnowledgeBaseManager` 拥有，SHALL NOT 以模块级全局变量管理。平台 `server.py` lifespan 与评测 bootstrap SHALL 调用 `noesis.knowledge.runtime` 的初始化与关闭方法。检索门面 `KbRetrievalService` SHALL 保持无状态，从管理器取得客户端。

#### Scenario: 客户端不泄漏为模块全局

- **WHEN** 审查 `noesis.knowledge.implementations.qdrant`
- **THEN** SHALL NOT 存在模块级 `_qdrant_client` / `_is_connected` 可变全局
- **AND** 客户端实例 SHALL 经管理器方法取得

#### Scenario: lifespan 初始化与关闭

- **WHEN** 平台服务启动或关闭
- **THEN** SHALL 调用 `noesis.knowledge.runtime` 与 `noesis.storage.pg_manager` 的初始化 / 关闭入口
- **AND** 评测嵌入式运行时 SHALL 能在不启动 FastAPI 的情况下初始化 manager

### Requirement: 数据层与知识库子系统 SHALL 懒加载

`noesis.storage`、`noesis.repositories` 与 `noesis.knowledge` 公共门面 SHALL 仿 `noesis.runtime` 的懒加载模式，`import noesis` / `import noesis.factory` SHALL NOT 把这些子系统的重型子模块装入 `sys.modules`，SHALL NOT 触发 DeepDoc ONNX / PaddleOCR 等重型解析依赖。重型依赖 SHALL 在首次实际调用时按需 import。`packages/harness/pyproject.toml` SHALL 显式声明数据访问与引擎直接依赖（至少 `qdrant-client`、`sqlalchemy`、`asyncpg`、`alembic`）；DeepDoc 重型依赖保持可选 / 懒加载。

#### Scenario: 工厂导入不触发重型依赖

- **WHEN** 在未安装 ONNX / PaddleOCR 的环境执行 `import noesis.factory`
- **THEN** SHALL 成功
- **AND** `noesis.knowledge` / `noesis.storage` 重型子模块 SHALL NOT 出现在 `sys.modules`

#### Scenario: 门面按需加载

- **WHEN** 执行 `from noesis.storage.postgres.manager import pg_manager` 或 `from noesis.knowledge import KbRetrievalService`
- **THEN** SHALL 按需加载对应子模块与 `sqlalchemy` / `qdrant-client` 依赖
- **AND** SHALL NOT 因门面导入而失败（缺重型解析依赖时仅在实际解析文档时失败）

### Requirement: 平台 HTTP 边缘 SHALL 薄化并直调 harness

平台 `noesis_server/api/*.py` SHALL 为薄 FastAPI router，在 HTTP 边缘做 `ResponseUtil` 包装与 `HTTPException` 映射，直调 `noesis.knowledge` / `noesis.repositories`，SHALL NOT 保留夹在 API 与引擎/数据层之间的厚重应用 service 层。平台 SHALL catch `noesis.knowledge` 抛出的 domain exception 并映射 HTTP 状态码（不存在→404、冲突→409、向量库未连接→503、未预期→500）。`noesis.knowledge` 与 `noesis.storage` SHALL NOT import FastAPI、`HTTPException` 或 `ResponseUtil`。平台 service 可保留跨多 repository 的薄编排，但 SHALL NOT 内联 DB 访问或持有 ORM。

#### Scenario: API 直调 harness

- **WHEN** 客户端请求知识库集合或检索
- **THEN** API SHALL 调用 `noesis.knowledge.runtime.knowledge_base` 单例或 `noesis.repositories`
- **AND** SHALL NOT 经平台 fat service 中转

#### Scenario: HTTP 翻译在边缘

- **WHEN** `knowledge_base` 抛 `KBNotFoundError`
- **THEN** API SHALL 映射为 HTTP 404
- **AND** `noesis.knowledge` SHALL NOT import `HTTPException`

#### Scenario: 平台不保留知识库应用 service

- **WHEN** 扫描 `noesis_server/services/`
- **THEN** SHALL NOT 存在 `knowledge_base_service.py` 或 `kb_collection_config_service.py`
- **AND** 知识库领域逻辑 SHALL 位于 `noesis.knowledge.manager`
