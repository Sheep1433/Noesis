## Why

Noesis 早期把 `noesis` 定义成可脱离平台的纯 Agent harness，导致业务 service、数据访问、知识库和交付运行时被拆到多个命名空间，制造了大量依赖注入与转发调用。

知识库是最典型的症状：引擎在平台域 `noesis_server/kb/`，已静态依赖 harness（`noesis.config.env` / `noesis.runtime.logging`）且不碰平台 service，却被 `agent-harness` 的「harness 禁止 import kb」规则挡在 harness 外，于是 harness 工具要经 `noesis.runtime.deps` 的五个 `require_*` + `bind_kb_*` / `temporary_kb_runtime` 反向注入才能拿到检索门面、Qdrant 客户端与执行参数。这套注入是纯历史包袱。

但根因不在知识库，而在 **harness 的定位被划成了「只管 Agent 内核，不碰业务数据」**。三组事实说明这个定位本身有问题：

1. **harness 早已持有 DB 配置与 Postgres 依赖** — `noesis.config.env.DataBaseConfig`（`env.py:751`）含全部 `postgres_*` 与 pool 设置；平台 `infrastructure/database/engine.py:7` 反而 `from noesis.config.env import DataBaseConfig` 拼 URL，即平台 engine 只是 harness 配置的壳。`pyproject.toml` 已声明 `langgraph-checkpoint-postgres` + `psycopg-pool`，SQLAlchemy 在传递依赖闭包内。
2. **平台已有 repository 模式，却没走完** — `noesis_server/infrastructure/database/repositories/` 已有 `agent_run` / `auth` / `settings` 三个 repository，与成熟后端的 `persistence/` 思路一致，只是没迁进 harness、没覆盖全量域。
3. **同形态后端已验证正确做法** — 与 Noesis 同为 `packages/harness`（内核包）+ `app/`（平台）拆分的成熟 Agent 后端，其 harness 包内含 `persistence/`，装**全部**业务 ORM（User / Run / Agent / 会话 / 反馈 / 定时任务 / 渠道连接）+ engine + Alembic 迁移，平台 `app/` 只剩 HTTP gateway / 渠道 / 调度边缘。这证明「harness 拥有全量数据层、平台只做边缘交付」是这种 Agent 后端的成熟分层，而非越界。

因此本变更不是「搬知识库」，而是采用两层后端：`backend/server` 是 FastAPI、middleware、lifespan 与进程入口；`backend/packages/noesis-core/src/noesis` 是**核心后端包**，拥有 Agent、应用 service、领域模型、交付运行实现、知识库和全量数据层。它保留 DeerFlow 的独立 wheel 能力，同时采用 YuXi 的核心包职责；物理目录与 distribution 统一改名为 `noesis-core`，不再用 `harness` 表示整个后端核心。

## What Changes

- 新增 harness 子系统 `noesis.storage`：`postgres/manager.py`（`PostgresManager` 单例 `pg_manager`，从 `DataBaseConfig` 建 async/sync engine + session factory + inspector，替代平台 `infrastructure/database/engine.py` + `dependency.py`，并接管 `migrations.py` 的 `run_migrations`/legacy stamp 启动逻辑）、`postgres/base.py`（单一 `Base(AsyncAttrs, DeclarativeBase)`，全量 ORM 共用，原定义在 `engine.py`）、`postgres/models/`（全量 ORM：知识库 `kb_collection_config` + 业务 `t_user` / `t_user_session` / `t_chat_session` / `t_chat_message` / `t_agent_run` / `t_agent_delivery` / `t_chat_attachment` / `user_scheduled_tasks` / `user_scheduled_task_runs` / `user_notification_preferences` / `user_settings_audit` 全部从 `noesis_server/models` 迁入）、`migrations/`（Alembic 三件套 `alembic.ini` + `env.py` + `versions/` 从 backend 根 `alembic/` 迁入，`env.py` 改指向 `noesis.storage`）。`noesis.config.checkpointer`（LangGraph checkpoint，独立库，psycopg 原生池）与 `pg_manager` 并存不合并。
- 新增 harness 子系统 `noesis.repositories`：知识库域 `kb_collection_config_repository`（从 `kb_collection_config_service.py` DB 方法提取，构造注入 `__init__(self, db)`，session 来源统一为 `pg_manager.get_async_session_context()`，`get_db()` 改委托 `pg_manager`，72 处 `Depends(get_db)` 签名不动，保留请求级事务语义）。业务域 repository（`agent_run`/`auth`/`settings` 等）暂留平台 `infrastructure/database/repositories/`（依赖平台 `domain/`，见下），仅改 import harness 的 `Base`/engine。
- 将 `noesis_server/kb/` 整体迁入 `noesis.knowledge`：`base.py`（`KnowledgeBase` ABC + domain exception + `FileStatus`）、`factory.py`、`manager.py`（用 repository 读配置、own Qdrant 客户端生命周期、领域方法抛 domain exception）、`runtime.py`（单例 `knowledge_base`）+ 子包 `parser/` / `chunking/` / `retrieval/` / `rerank/` / `embedding/` / `implementations/qdrant.py`（原 `qdrant.py` 全局态迁入 manager）/ `deepdoc/` / `_ragflow_compat/` / `utils/` / `seed/`。
- 平台 `knowledge_base_service.py` 领域逻辑并入 `noesis.knowledge.manager`，HTTP 翻译留 `noesis_server/api/knowledge_base_api.py` 边缘；`kb_collection_config_service.py` DB 方法并入 `noesis.repositories`；二者删除。
- 平台 `infrastructure/database/{engine.py,dependency.py,migrations.py}` 删除，迁入 `noesis.storage`；`infrastructure/database/repositories/`（`agent_run`/`auth`/`settings`）**暂留平台**，改 import harness 的 `Base`/engine（经 re-export 或直接 `noesis.storage`）。原因：这 3 个 repo 依赖 `noesis_server.domain.*`（`RunStatus`/`AuthUser` 等平台交付领域，6257 行 SSE/delivery/HITL/run-manager），将它们迁入 harness 会强制把整个平台 domain 层一并下沉，是独立的大变更，超出本变更范围且危及端到端可用性。本变更完成 harness 拥有 storage/engine/ORM/Alembic 与知识库域 repository；业务域 repository 与 `domain/` 的下沉留作后续独立 change。
- harness 工具 `kb_search_tool.py` 直接 import `noesis.knowledge`；`noesis.runtime.deps` 删除全部 KB 绑定面（`bind_kb_services` / `bind_kb_retrieval` / `bind_vlm` KB 部分 / `temporary_kb_runtime` / 五个 `require_*`）；VLM 判定随 `embedding` 迁入 `noesis.knowledge`。
- 平台 `services/harness_wiring.py` 删除 KB 绑定调用；`server.py` lifespan 改调 `noesis.storage.pg_manager` + `noesis.knowledge.runtime` 初始化/关闭；平台 service（auth / chat / run / scheduled_task / settings / mcp / memory 等）改用 `noesis.repositories`。
- `packages/noesis-core/pyproject.toml` 显式声明核心包直接使用的依赖；DeepDoc 重型依赖保持懒加载。
- 更新 `agent-harness` spec：`noesis` 重新定义为核心后端包，拥有 Agent、service、domain、delivery runtime、数据层与知识库；`server` 只负责 HTTP、middleware、lifespan 与进程入口。核心包 SHALL NOT import `server.*`，`noesis.factory` 仍须可在不启动 FastAPI 的情况下导入。
- 非目标：不引入第二套向量库后端（ABC 仅 Qdrant 单实现）；不改写 DeepDoc 解析算法；不改变对外 HTTP API 路径与字段；不改变 Qdrant payload schema 与已入库数据；不改变 DB schema 与表名（仅迁移 ORM 定义位置）；不新增知识图谱 / 多模态检索（属既有 change 范围）；不重写 LangGraph/LangChain Agent 框架。

## Capabilities

### Modified Capabilities

- `agent-harness`：`noesis` 定位重新定义为「核心后端包」。`noesis.services` / `domain` / `agents` / `storage` / `repositories` / `knowledge` 为同一核心包的一等子系统；`server` 是进程与 HTTP 边缘。KB 依赖注入面全部删除，反向依赖断言强化为「noesis 不得 import server」。
- `knowledge-base`：DeepDoc 解析、分块、hybrid 检索门面、rerank、嵌入、集合配置持久化的实现位置由 `noesis_server.kb.*` / `noesis_server.services.*` / `noesis_server.models.*` 迁至 `noesis.knowledge.*` / `noesis.repositories.*` / `noesis.storage.*`；平台保留薄 HTTP API；Qdrant 成为 `KnowledgeBase` ABC 的具体实现；manager 直连 repository 读配置。

## Impact

- **核心包新增**：`noesis.storage/`（postgres/manager + models 全量 + migrations）、`noesis.repositories/`（全量 repo）、`noesis.knowledge/`（含 deepdoc / _ragflow_compat 平移）；`tools/kb_search_tool.py` 改直接 import；`runtime/deps.py` 删 KB 绑定面；`pyproject.toml` 声明核心包直接依赖。
- **核心包删除**：`runtime/deps.py` 的全部 KB 绑定与 `require_*`；`runtime/__init__.py` `_EXPORTS` 同步。
- **平台删除**：`noesis_server/kb/`、`services/knowledge_base_service.py`、`services/kb_collection_config_service.py`、`infrastructure/database/`（engine/dependency/migrations/repositories）、`models/`（全量 ORM 迁入 harness）、backend 根 `alembic/` + `alembic.ini`（迁入 `noesis.storage.migrations`）。
- **平台改造**：`api/knowledge_base_api.py`（薄化）、`services/harness_wiring.py`、`services/chat_attachment_service.py`、`services/settings_diagnostics_service.py`、`bootstrap/kb.py`、`server.py`、`middleware/csrf.py`、`sql/{initialize_postgresql,rotate_registration_invite}.py`，以及所有曾用 `noesis_server.infrastructure.database` 或 `noesis_server.models` 的平台 service/API（auth / chat / run / scheduled_task / settings / mcp / memory / user 等）改 import `noesis.storage` / `noesis.repositories`。`get_db()` 改委托 `pg_manager`（72 处 Depends 签名不动）。
- **评测**：`evals/bootstrap.py`、`evals/case/rag/ingest.py`、`evals/case/rag/provider.py` 改 import；`temporary_kb_runtime` 删除，评测直接用 `noesis.knowledge.runtime` + `noesis.storage`。
- **测试**：约 18 个 `test_kb_*.py` / `test_document_parser.py` / `test_embedding_config.py` 更新 import 与 `@patch` 目标；DB engine / repository 消费者测试同步；`test_harness_package_boundary.py` 调整禁用集合 + 新增反向依赖断言。
- **文档**：`docs/architecture/knowledge-base.md` 分层图重写、新增 harness 数据层架构说明；`docs/NOTES.md` DeepDoc vendor 路径同步；`openspec/specs/agent-harness` 与 `knowledge-base` 主规格归档时对齐。
- **打包**：`noesis-core` wheel 含全量数据层 + 知识库引擎；`test_built_wheel_imports_outside_backend` 扩展可 import `noesis.storage` / `noesis.knowledge` 门面。
- **兼容性**：不新增/删除 `/api/kb` 端点；Qdrant 数据与 payload 不变；DB schema 与表名不变（仅迁移 ORM 定义位置）；检索结果字段与 evidence identity 语义不变；import 路径变更属仓内重构，不对外暴露。
