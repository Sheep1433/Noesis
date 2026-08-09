## MODIFIED Requirements

### Requirement: noesis SHALL 是可安装的核心后端包

系统 SHALL 将 Agent、应用 service、domain、runtime、LLM、知识库和数据基础设施置于 workspace distribution `noesis-core` 的唯一顶层 Python 包 `noesis`。核心包目录 SHALL 为 `backend/packages/noesis-core`；`harness` SHALL 只作为 Agent 运行测试的通用概念，不再作为核心包目录或 distribution 名称。

#### Scenario: 核心能力使用统一命名空间

- **WHEN** API、评测、CLI 或后台运行时加载核心能力
- **THEN** SHALL 从 `noesis.agents` / `noesis.services` / `noesis.domain` / `noesis.knowledge` / `noesis.repositories` / `noesis.storage` 导入
- **AND** SHALL NOT 存在第二套平台 service、domain、ORM 或 repository 命名空间

#### Scenario: Agent 工厂保持独立可导入

- **WHEN** 离线评测或 CLI 执行 `from noesis.factory import create_noesis_agent`
- **THEN** SHALL 不要求启动 FastAPI app
- **AND** SHALL 不预先加载 DeepDoc、Qdrant client 或业务数据库连接

### Requirement: server SHALL 只承担进程与 HTTP 边缘

`backend/server` SHALL 只包含 FastAPI app、router、middleware、HTTP response/exception 映射、lifespan/bootstrap 与进程装配。业务用例、Agent、交付运行实现、scheduler、渠道运行实现、知识库和数据层 SHALL 位于 `noesis`；其启动和关闭由 `server.main` lifespan 调用。

#### Scenario: 单向依赖

- **WHEN** AST 扫描 `packages/noesis-core/src/noesis/**/*.py`
- **THEN** SHALL 不存在 `server.*` import
- **AND** `server.*` MAY import `noesis.*`

#### Scenario: HTTP 组件留在 server

- **WHEN** 扫描 FastAPI router、middleware、ResponseUtil 与全局 exception handler
- **THEN** SHALL 只在 `backend/server` 注册或装配
- **AND** `noesis` SHALL NOT 创建 FastAPI app 或 APIRouter

### Requirement: 核心子系统依赖 SHALL 保持单向

`noesis.storage` SHALL 拥有唯一 ORM Base、engine、session factory 与 Alembic；`noesis.repositories` SHALL 封装跨用例共享或具有独立持久化语义的查询；`noesis.knowledge` SHALL 经 repository 访问集合配置。三者 SHALL NOT import `noesis.services` 或 `server`。

局部事务查询 MAY 在 application service 中直接使用注入的 `AsyncSession`，前提是不得新建 engine、不得复制共享查询、不得破坏跨表事务。系统 SHALL NOT 为单次简单查询创建只做转发的 repository。

#### Scenario: DB engine 唯一来源

- **WHEN** service、repository 或 Knowledge 需要业务数据库 session
- **THEN** session SHALL 来自 `noesis.storage.postgres.manager.pg_manager`
- **AND** `server` SHALL NOT 定义第二套 engine 或 ORM Base

#### Scenario: 共享查询进入 repository

- **WHEN** 同一持久化查询被多个 service/Agent/评测调用，或承担聚合加载与并发更新语义
- **THEN** SHALL 位于 `noesis.repositories`
- **AND** service SHALL 共享请求级 session 控制 commit/rollback

### Requirement: Knowledge SHALL 使用 factory-manager-runtime

`noesis.knowledge` SHALL 提供 `KnowledgeBase`、`KnowledgeBaseFactory`、`KnowledgeBaseManager` 与 runtime 单例。manager SHALL 拥有 Qdrant client 生命周期，并通过 factory 创建绑定当前 client 的实现；Qdrant adapter SHALL NOT 保存模块级可变 client/connection 状态。

#### Scenario: Knowledge 生命周期

- **WHEN** `server.main` 或评测运行时启动/关闭 Knowledge
- **THEN** SHALL 调用 `noesis.knowledge.runtime` 的 initialize/close 入口
- **AND** client 状态 SHALL 由 `KnowledgeBaseManager` 实例持有

### Requirement: 核心包 SHALL 声明直接依赖并保持懒加载

`packages/noesis-core/pyproject.toml` SHALL 声明核心包直接使用的数据与知识库依赖，包括 `qdrant-client`、`sqlalchemy`、`asyncpg`、`psycopg[binary,pool]`、`alembic`、`python-docx` 与直接导入的 LangChain provider/splitter。DeepDoc 重型依赖 MAY 由 workspace 提供，但 `import noesis` / `import noesis.factory` SHALL 不加载它们。

#### Scenario: wheel 门面导入

- **WHEN** 从构建 wheel 导入 `noesis.factory`、`noesis.storage`、`noesis.repositories` 与 `noesis.knowledge` 门面
- **THEN** SHALL 不依赖 backend 源码目录出现在 `PYTHONPATH`
- **AND** SHALL 不触发 ONNX、OCR 或 DeepDoc parser 导入

### Requirement: checkpoint 与业务数据库 SHALL 分离

`noesis.config.checkpointer` SHALL 管理 LangGraph checkpoint 的 psycopg 原生池；`noesis.storage.postgres.manager` SHALL 管理业务 ORM 的 SQLAlchemy engine。两者 MAY 使用不同数据库，SHALL NOT 合并生命周期或连接实现。

#### Scenario: 两类连接分别关闭

- **WHEN** 后端进程结束 lifespan
- **THEN** SHALL 分别调用 checkpointer 与 `pg_manager` 的关闭入口
- **AND** 任一管理器 SHALL NOT 代替另一个管理器释放连接
