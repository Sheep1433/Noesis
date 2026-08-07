# Design — sink-data-layer-into-harness

> 配套 `proposal.md`。本变更重新定义 harness 边界：harness 从「Agent 内核」扩展为「Agent 内核 + 全量数据层」，平台降为薄边缘。受影响能力：`agent-harness`、`knowledge-base`。

## 1. 现状（已核对源码）

### 1.1 数据访问层散落在平台域

| 现路径 | 职责 | 迁移去向 |
|---|---|---|
| `noesis_server/infrastructure/database/engine.py` | async/sync engine + URL + **`Base(AsyncAttrs, DeclarativeBase)`** + `AsyncSessionLocal` + `inspector` | `noesis.storage.postgres.manager` |
| `noesis_server/infrastructure/database/dependency.py` | `get_db()`（请求级 session）+ `init_database()`（调 `run_migrations` + 连接校验）+ `get_inspector()` | `get_db` 改委托 `pg_manager`；`init_database` 并入 `pg_manager.initialize` |
| `noesis_server/infrastructure/database/migrations.py` | `run_migrations()` **函数**（含 `_bootstrap_legacy_schema_stamp` legacy stamp 逻辑） | `noesis.storage.postgres.manager` 或 `noesis.storage.migrations` |
| `noesis_server/infrastructure/database/repositories/{agent_run,auth,settings}.py` | 3 个 repository，**构造注入** `__init__(self, db: AsyncSession)` | `noesis.repositories` |
| `noesis_server/models/{db_models,chat_models,scheduled_task_models,settings_models,kb_models}.py` | 全量 ORM（11 表），均 `from ...engine import Base` | `noesis.storage.postgres.models`（按域分子模块） |
| `alembic/`（backend 根）+ `alembic.ini` | Alembic `env.py` + 14 个 `versions/*.py`；`env.py` import 4 个 models + engine 的 `Base`/URL | `noesis.storage.migrations` |
| `noesis_server/kb/` | 解析/分块/嵌入/检索/rerank/Qdrant 适配 | `noesis.knowledge` |
| `noesis_server/services/knowledge_base_service.py` | 领域逻辑 + HTTP 编排混杂 | 领域并入 `noesis.knowledge.manager`，HTTP 留 API |
| `noesis_server/services/kb_collection_config_service.py` | 集合配置 DB 持久化 + 自建 sync engine | `noesis.repositories` |
| `sql/{initialize_postgresql.py,rotate_registration_invite.py}` | DB 初始化/邀请轮换脚本，import engine URL | 改 import `noesis.storage` |
| `noesis_server/middleware/csrf.py` | 用 `infrastructure.database` | 改 import `noesis.storage` |

### 1.2 关键事实

- **`Base` 定义在 `engine.py`**，所有 ORM model `from noesis_server.infrastructure.database.engine import Base`。迁移须保证**单一 `Base`**（否则 `metadata` 分裂，`create_all` / autogenerate 失效）。`Base` 随 engine 迁入 `noesis.storage.postgres`，`AsyncAttrs` 依赖同迁。
- **Alembic 三件套**：`alembic.ini`（`script_location = alembic`，相对 backend 根）+ backend 根 `alembic/`（`env.py` + `versions/`）+ `migrations.py` 的 `run_migrations()` 函数。`env.py` 显式 import 4 个 `noesis_server.models.*` 与 engine 的 `SYNC_SQLALCHEMY_DATABASE_URL` / `Base`，迁移后全要改指向 `noesis.storage`。`migrations.py` 的 `_bootstrap_legacy_schema_stamp`（检测旧 `init_sql` 建表无 revision 时 stamp head）**必须保留**，否则已部署环境迁移断裂。
- **`Depends(get_db)` 被用 72 处**（API 层）。`get_db` 内部 `async with AsyncSessionLocal() as current_db: yield`。迁移只改 `get_db` 内部委托 `pg_manager`，72 处 Depends 签名不动。
- **lifespan**（`server.py`）：`init_database()` → `AsyncSessionLocal()`（recovery）→ `init_checkpointer()`；关闭 `close_checkpointer()` + run_manager。**现状未显式关 async_engine**——迁移时 `pg_manager.close()` 补上。
- **langgraph checkpointer 已在 harness**：`packages/harness/noesis/config/checkpointer.py` 用 `psycopg.AsyncConnectionPool` + `AsyncPostgresSaver` 连**独立 checkpoint 库**（`get_config.get_checkpoint_config().postgres_database`，可能与业务库异名）。本变更**不合并** checkpointer 与 `pg_manager`：checkpointer 管 LangGraph checkpoint（psycopg 原生连接池），`pg_manager` 管 ORM 业务库（SQLAlchemy async engine），二者职责不同、库可能不同，并存。spec 须明确边界避免误以为重复。

### 1.3 全量 ORM（11 表，全部迁入）

| 现文件 | `__tablename__` | 迁入子模块 |
|---|---|---|
| `models/db_models.py` | `t_user`、`t_user_session` | `models/auth.py` |
| `models/chat_models.py` | `t_chat_session`、`t_chat_message`、`t_agent_run`、`t_agent_delivery`、`t_chat_attachment` | `models/chat.py` |
| `models/scheduled_task_models.py` | `user_scheduled_tasks` | `models/scheduled_task.py` |
| `models/settings_models.py` | `user_scheduled_task_runs`、`user_notification_preferences`、`user_settings_audit` | `models/settings.py` |
| `models/kb_models.py` | `kb_collection_config` | `models/knowledge.py` |

### 1.4 全量消费者（import `noesis_server.models` 或 `infrastructure.database` 的）

平台 service：`auth/{invites,sessions}`、`channel_run_service`、`chat_attachment_service`、`chat_service`、`hitl_timeout`、`kb_collection_config_service`、`login_service`、`memory_dream_{scheduler,service}`、`notification_preference_service`、`qa/{helpers,service}`、`run_recovery_service`、`run_service`、`scheduled_task_{scheduler,service}`、`settings_{diagnostics,service,transfer}_service`、`user_service`。
平台 API：`auth_api`、`chat_api`、`chat_attachment_api`、`knowledge_base_api`、`mcp_api`、`settings_api`、`user_api`、`user_settings_api`。
其他：`bootstrap/kb.py`、`middleware/csrf.py`、`server.py`、`alembic/env.py`、`sql/{initialize_postgresql,rotate_registration_invite}.py`、`evals/kb/run.py`。
测试：约 9 个（`test_auth_domain_boundary`、`test_automation_runs`、`test_batch_delete_sessions`、`test_chat_service_user_id`、`test_chat_session_*cleanup`、`test_run_api_contract`、`test_session_title_once`、`test_agent_run_repository`、`test_settings_*`、`integration/test_message_sequence_postgres`）。

## 2. 目标结构

```
packages/harness/noesis/
├── storage/
│   ├── postgres/
│   │   ├── manager.py          # PostgresManager 单例 pg_manager：engine + session factory + inspector + init/close；含 run_migrations + legacy stamp
│   │   ├── base.py             # Base(AsyncAttrs, DeclarativeBase) —— 单一 Base，全量 ORM 共用
│   │   └── models/             # 全量 ORM（按域）
│   │       ├── __init__.py     # 注册入口（import 全部 model 确保 metadata 注册）
│   │       ├── auth.py  chat.py  scheduled_task.py  settings.py  knowledge.py
│   └── migrations/             # Alembic（alembic.ini + env.py + versions/ 从 backend 根迁入）
│       ├── alembic.ini
│       ├── env.py              # 改 import noesis.storage.postgres.{base,models}
│       └── versions/           # 14 个版本文件整体平移，不改 revision_id
├── repositories/               # 全量 repository（构造注入 session，session 由 pg_manager 提供）
│   ├── kb_collection_config_repository.py
│   ├── agent_run_repository.py  auth_repository.py  settings_repository.py   # 从 infrastructure/database/repositories 迁入
│   ├── user_repository.py  chat_session_repository.py  chat_message_repository.py  scheduled_task_repository.py
├── knowledge/                  # 从 noesis_server/kb 迁入
│   ├── base.py  factory.py  manager.py  runtime.py  schemas.py  read_models.py
│   ├── parser/ chunking/ retrieval/ rerank/ embedding/ implementations/qdrant.py
│   ├── deepdoc/ _ragflow_compat/ utils/ seed/  README.md  download_models.py
├── config/
│   └── checkpointer.py         # 既有，不动（独立 checkpoint 库，与 pg_manager 并存）
├── agents/ tools/ runtime/ llm/ ...
```

### 2.1 storage.postgres.manager + base

```
# base.py
class Base(AsyncAttrs, DeclarativeBase): pass   # 单一 Base，从 engine.py 迁入

# manager.py
class PostgresManager(metaclass=SingletonMeta):
    def initialize(self): ...           # 建 async_engine + async_sessionmaker + sync_engine + inspector；执行 run_migrations() + 连接校验（替代 init_database）
    def get_async_session_context(self): ...   # async context manager，供编排 service / get_db / 评测用
    def get_sync_session(self): ...     # 供 Agent 工具线程内同步读 / Alembic
    def get_inspector(self): ...
    async def close(self): ...          # 关 async_engine + sync_engine（补现状未关 engine 的缺口）
pg_manager = PostgresManager()
```

`run_migrations()` 与 `_bootstrap_legacy_schema_stamp()` 从 `migrations.py` 迁入 manager（或 `noesis.storage.migrations` 模块），保留 legacy stamp 逻辑；`alembic.ini` 的 `script_location` 改指向 `noesis.storage.migrations`。

### 2.2 repositories —— 构造注入，session 由 pg_manager 提供

**repo 签名不变**（`__init__(self, db: AsyncSession)`），保持请求级事务共享语义。session 来源从平台 `AsyncSessionLocal` 换成 harness `pg_manager`：

- **API 路径**：`get_db()` 改为 `async with pg_manager.get_async_session_context() as db: yield db`；72 处 `Depends(get_db)` 签名不动。service 收 `db` → 构造 `XxxRepository(db)` → 操作 → `await db.commit()`，事务语义不变。
- **编排 service 跨 repo 事务**：`async with pg_manager.get_async_session_context() as db:` 内构造多个 repo（`AgentRunRepository(db)` + `ChatMessageRepository(db)`），共享同一 session，service 统一 commit。
- **评测/嵌入式**：同样 `async with pg_manager.get_async_session_context() as db:` 用 repo，不依赖 FastAPI Depends。
- **Agent 工具线程内同步读**：`pg_manager.get_sync_session()`（替代 `kb_collection_config_service` 自建 sync engine）。

> 不采用「repo 每方法自取 session」模式——Noesis 有真实跨表事务（`chat_service` 一次请求写 session+message+attachment+run，多处 `commit`），自取模式破坏原子性。pg_manager 作为 session 唯一来源已使 harness 自包含，无需 repo 自取。

### 2.3 knowledge.manager

`KnowledgeBaseManager` 取代 `qdrant.py` 全局态 + `knowledge_base_service` 领域逻辑：own Qdrant 客户端生命周期（`init`/`close`，无模块级全局）；领域方法（create/upload/search/delete）调 `implementations/qdrant.py` 与 `noesis.repositories`；抛 domain exception（`KBNotFoundError`/`KBNameConflictError`/`QdrantNotConnectedError`/`KBOperationError`），不 import FastAPI/ResponseUtil。`runtime.py` 单例 `knowledge_base` 注册 Qdrant 实现。

### 2.4 抽象基类与工厂

`base.py` 的 `KnowledgeBase` ABC 声明检索/整篇拉取/集合信息/入库等**现有真实被调用**的接口；`implementations/qdrant.py` 的 `QdrantKB(KnowledgeBase)` 为唯一实现。**SHALL NOT** 落地第二套后端，SHALL NOT 在 ABC 预声明无调用方方法。

## 3. 调用链（迁移后）

```
noesis_server/api/*.py   (FastAPI router; Depends(get_db) 签名不变，get_db 内部委托 pg_manager)
  └─ service(db) → XxxRepository(db) → await db.commit()      (事务语义不变)
      └─ session 来源: noesis.storage.postgres.manager.pg_manager
      └─ ORM: noesis.storage.postgres.models.*  (单一 Base)
noesis_server/api/knowledge_base_api.py  →  noesis.knowledge.runtime.knowledge_base  (直调，catch domain exception → HTTP)
noesis.tools.kb_search_tool  →  from noesis.knowledge import ...   (直 import，无 deps 注入)
noesis.*  ─imports─▶ noesis.config / noesis.runtime.logging / noesis.storage / noesis.repositories  (闭包内闭合)
noesis_server.*  ─imports─▶ noesis.*   (平台单向消费 harness)
noesis.config.checkpointer  (既有，独立 checkpoint 库，与 pg_manager 并存，不动)
```

## 4. 依赖与边界

### 4.1 harness 反向依赖断言

- `noesis.storage/**` / `noesis.repositories/**` / `noesis.knowledge/**` **SHALL NOT** import `noesis_server.*` / `services` / `domain` / `api`。
- `noesis.storage` SHALL 只依赖 `noesis.config.env.DataBaseConfig` 与 SQLAlchemy/psycopg/alembic。
- 平台 `noesis_server/**` 单向 import `noesis.*`，SHALL NOT 保留 engine/ORM/repository/Alembic 实现。

### 4.2 deps 收敛

`noesis/runtime/deps.py` 删除全部 KB 绑定面（`bind_kb_services`/`bind_kb_retrieval`/`temporary_kb_runtime`/`bind_vlm` KB 部分）与 `require_kb_*` / `require_is_vlm_configured`；`runtime/__init__.py` `_EXPORTS` 同步。VLM 判定随 `embedding` 迁入 `noesis.knowledge`。

### 4.3 HTTP/领域分界

平台 API 边缘化：调 `knowledge_base.xxx()` 或 `repository.xxx(db)`，catch domain exception → HTTP（404/409/503/500），`ResponseUtil` 包装。`get_db()` 改委托 `pg_manager`，72 处 Depends 不动。平台 service 保留跨多 repo 的薄编排（如 `chat_service` 聚合消息+附件+run），在 `pg_manager` session context 内构造多 repo 共享事务，SHALL NOT 内联 ORM/engine。

### 4.4 checkpointer 边界

`noesis.config.checkpointer`（LangGraph checkpoint，psycopg 原生池，独立库）与 `noesis.storage.postgres.manager`（ORM 业务库，SQLAlchemy engine）**并存不合并**。两者职责与连接技术不同。spec 须声明此边界，避免误删或误合并。

## 5. 打包与懒加载

`pyproject.toml` 新增 `qdrant-client`/`sqlalchemy>=2.0`/`asyncpg`/`alembic`（`psycopg-pool` 已有）。`noesis.storage` / `noesis.repositories` / `noesis.knowledge` 门面仿 `noesis.runtime` 懒加载（`__getattr__`）：`import noesis`/`import noesis.factory` 不触发这些子系统重型模块；缺 ONNX/PaddleOCR 时仅实际解析失败。`test_built_wheel_imports_outside_backend` 扩展可 import `noesis.storage`/`noesis.knowledge`/`noesis.repositories` 门面。

## 6. 迁移阶段

### 阶段 A：storage 与 repository 骨架（不改运行时行为）
1. 新建 `noesis.storage.postgres.{base,manager}`：`Base` 从 `engine.py` 迁入；`PostgresManager` 单例 `pg_manager`（engine + session factory + inspector + `run_migrations` + legacy stamp + `init`/`close`），从 `DataBaseConfig` 建。
2. `noesis.storage.postgres.models/`：11 表全量从 `noesis_server/models` 迁入（按域子模块），改 `from noesis.storage.postgres.base import Base`；`models/__init__.py` 注册入口。保持表名/字段不变。
3. Alembic 三件套迁入 `noesis.storage.migrations`：`alembic.ini`（`script_location` 改指 `noesis.storage.migrations`）+ `env.py`（改 import `noesis.storage.postgres.{base,models}`）+ `versions/`（整体平移，不改 revision_id）。跑 `alembic history` 校验链连续。
4. `noesis.repositories/`：3 个已有 repo（构造注入）从 `infrastructure/database/repositories` 平移；`kb_collection_config_repository` 新建（含 `load_query_params_sync` 经 `pg_manager.get_sync_session`）；其余业务 repo 按需提取。
5. `noesis_server/infrastructure/database/{engine,dependency,migrations}.py` 改为 re-export `noesis.storage`（过渡，阶段 D 删）：`get_db` 改委托 `pg_manager.get_async_session_context()`；`init_database` 委托 `pg_manager.initialize`。平台 consumer 暂不动。验证行为一致。
6. 更新 `test_harness_package_boundary.py` 禁用集合与反向依赖断言（阶段 E 前可临时跳过并记原因）。

### 阶段 B：knowledge 引擎平移
1. `git mv noesis_server/kb/{document_parse,chunk,retrieval,rerank,embedding,deepdoc,_ragflow_compat,seed,download_models.py,README.md}` → `noesis/knowledge/{parser,chunking,retrieval,rerank,embedding,deepdoc,_ragflow_compat,seed,download_models.py,README.md}`。
2. `qdrant.py` → `implementations/qdrant.py`（`QdrantKB(KnowledgeBase)`），全局态迁入 manager。
3. 新建 `noesis/knowledge/{base,factory,manager,runtime,schemas,read_models}.py`。
4. 全仓 `noesis_server.kb.*` → `noesis.knowledge.*` import 改写（`document_parse`→`parser`、`chunk`→`chunking`）。
5. 重建 `noesis/knowledge/__init__.py` 懒加载门面。`grep -rn "noesis_server.kb"` 清零。

### 阶段 C：领域逻辑并入 manager，删除 KB 注入
1. `knowledge_base_service.py` 领域方法迁入 `noesis.knowledge.manager`，抛 domain exception；HTTP 翻译留 API。
2. `kb_collection_config_service.py` DB 方法迁入 `noesis.repositories`（补齐并删平台 service）。
3. `tools/kb_search_tool.py` 改直接 import `noesis.knowledge`。
4. `runtime/deps.py` 删全部 KB 绑定面 + `require_*`；`runtime/__init__.py` `_EXPORTS` 同步；VLM 判定随 embedding 迁入。
5. `services/harness_wiring.py` 删 `bind_kb_*` 调用。

### 阶段 D：平台 data 层收敛与 API 薄化
1. `api/knowledge_base_api.py` 薄化：直调 `knowledge_base` 单例，catch domain exception → HTTP。
2. 删除 `noesis_server/infrastructure/database/`（engine/dependency/migrations/repositories）；所有平台 service/API/middleware/bootstrap/sql 改 import `noesis.storage`/`noesis.repositories`；`alembic/env.py` 已在阶段 A 迁入。`grep -rn "noesis_server.infrastructure.database"` 清零。
3. 删除 `noesis_server/models/`（全量 ORM 已迁入）；删除 `noesis_server/kb/`、`services/knowledge_base_service.py`、`services/kb_collection_config_service.py`；`grep -rn "noesis_server.models"` 清零。
4. `sql/{initialize_postgresql,rotate_registration_invite}.py`、`middleware/csrf.py`、`evals/kb/run.py` 改 import `noesis.storage`。
5. `server.py` lifespan 改调 `pg_manager.initialize()`（含迁移）+ `init_checkpointer()` + `noesis.knowledge.runtime` 初始化；关闭补 `pg_manager.close()` + `close_checkpointer()`。`bootstrap/kb.py` 改 import。

### 阶段 E：评测、测试、文档、回归
1. `evals/bootstrap.py`、`evals/case/rag/{ingest,provider}.py`、`evals/kb/run.py` 改 import；删 `temporary_kb_runtime`，评测用 `noesis.knowledge.runtime` + `pg_manager`。
2. 更新测试 import 与 `@patch` 目标（KB 约 18 个 + 约 9 个 DB/model 消费者测试 + `alembic/env.py` 相关）。
3. 启用 `test_harness_package_boundary.py` 新断言；跑全量回归 + Harbor E2E + Agentic RAG ingest + SuperAgent 流式冒烟。
4. `code-review` + `code-simplification` 收尾；更新文档。

## 7. 分支与回滚
全程在 `feat/sink-data-layer-into-harness` 分支（从最新 `dev` 拉，合 `dev → main`）。每阶段可编译可测；阶段 A–B 末尾为中间稳态。无数据迁移/schema 变更/payload 变更；最坏 `git revert`。过渡期 `infrastructure/database` 仅 re-export `noesis.storage`，非独立实现，阶段 D 删除，不长期并存。注意：`converge-agent-runtime` 亦改 `runtime/deps.py`（task 8.3 未完成），两 change 合并顺序须协调，以 deps.py 最终状态为准。

## 8. 边界测试调整
`test_harness_package_boundary.py`：`FORBIDDEN_PLATFORM_PACKAGES`/`LEGACY_PLATFORM_PACKAGES` 移除 `kb`；新增 `noesis.storage/**`/`noesis.repositories/**`/`noesis.knowledge/**` 不得 import `noesis_server.*` 断言；新增 `test_platform_has_no_data_layer`（平台无 `infrastructure/database/`、无 `models/`，engine/ORM/Alembic 唯一来源 `noesis.storage`）；`test_built_wheel_imports_outside_backend` 扩展可 import `noesis.storage`/`noesis.knowledge`/`noesis.repositories` 门面。

## 9. 风险与缓解
| 风险 | 缓解 |
|---|---|
| `get_db` 改委托 pg_manager 影响请求级 session 语义 | `get_db` 内部仍 `async with ... yield`，行为对齐原 `AsyncSessionLocal`；72 处 Depends 签名不动；事务由 service 在该 session commit，语义不变 |
| 全量 ORM 迁移波及所有平台 service/API/middleware/sql/evals | 阶段 D 逐 consumer 迁移；`grep -rn "noesis_server.models\|noesis_server.infrastructure.database"` 清零校验 |
| Alembic `versions/` 平移后版本链断裂 | 整体平移不改 revision_id；`env.py` 指向新 metadata；`alembic history` 校验链连续；legacy stamp 逻辑保留 |
| 单一 `Base` 分裂导致 metadata 不全 | `Base` 定义在 `noesis.storage.postgres.base`，全量 model 继承同一 `Base`；`models/__init__.py` 注册入口确保全注册 |
| checkpointer 与 pg_manager 误合并 | 明确边界：checkpointer（psycopg 原生池，独立 checkpoint 库）与 pg_manager（SQLAlchemy engine，业务库）并存不合并，checkpointer 不动 |
| lifespan 现状未关 async_engine | `pg_manager.close()` 补关 async+sync engine |
| 两套 engine 并存（阶段 A–D 过渡） | 过渡期 `infrastructure/database` 仅 re-export，非独立实现；阶段 D 删除 |
| DeepDoc vendored 平移内部 import 断裂 | `deepdoc/**`/`_ragflow_compat/**` 同级 import，整目录平移不断裂；阶段 B 跑 `test_kb_deepdoc*` |
| `@patch` 目标遗漏 | 阶段 E `grep -rn "noesis_server.kb\|noesis_server.infrastructure.database\|noesis_server.models"` 全仓清零 |
| ABC 单实现过度抽象 | 仅声明现有调用面方法，明确单实现，不引入第二套 |
| 与 `converge-agent-runtime` 都改 `runtime/deps.py` | 合并顺序协调，以 deps.py 最终状态为准 |

## 10. 与既有 change 的关系
`kb-multimodal-retrieval`（不冲突，只搬位置）、`converge-agent-runtime`（不动 runtime kernel，仅删 deps.py KB 绑定面，但都改 deps.py 须协调）、`add-agent-context-usage-attribution`（无依赖）。
