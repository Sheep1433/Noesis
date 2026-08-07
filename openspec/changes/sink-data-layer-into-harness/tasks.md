## 1. storage 与 repository 骨架（不改运行时行为）

- [ ] 1.1 新建 `noesis/storage/postgres/base.py`：`Base(AsyncAttrs, DeclarativeBase)` 从 `noesis_server/infrastructure/database/engine.py` 迁入（单一 Base，全量 ORM 共用）
- [ ] 1.2 新建 `noesis/storage/postgres/manager.py`：`PostgresManager` 单例（`pg_manager`，`SingletonMeta`），从 `noesis.config.env.DataBaseConfig` 建 async engine + sync engine + `async_sessionmaker` + inspector；提供 `get_async_session_context()` / `get_sync_session()` / `get_inspector()` / `initialize()`（含 `run_migrations` + 连接校验，替代 `init_database`）/ `close()`（关 async+sync engine，补现状未关缺口）；`run_migrations()` + `_bootstrap_legacy_schema_stamp()` 从 `infrastructure/database/migrations.py` 迁入并保留 legacy stamp 逻辑
- [ ] 1.3 新建 `noesis/storage/postgres/models/`：11 表全量从 `noesis_server/models` 迁入（`auth.py`: t_user/t_user_session；`chat.py`: t_chat_session/t_chat_message/t_agent_run/t_agent_delivery/t_chat_attachment；`scheduled_task.py`: user_scheduled_tasks/user_scheduled_task_runs；`settings.py`: user_notification_preferences/user_settings_audit；`knowledge.py`: kb_collection_config）；改 `from noesis.storage.postgres.base import Base`；`models/__init__.py` 注册入口确保 metadata 全注册；保持表名/字段不变
- [ ] 1.4 Alembic 三件套迁入 `noesis/storage/migrations/`：`alembic.ini`（`script_location` 改指 `noesis.storage.migrations`）+ `env.py`（改 import `noesis.storage.postgres.{base,models}` 与 `pg_manager` 的 sync URL）+ `versions/`（14 个整体平移，不改 revision_id）；跑 `alembic history` 校验链连续
- [ ] 1.5 新建 `noesis/repositories/`：3 个已有 repo（`agent_run`/`auth`/`settings`，构造注入 `__init__(self, db)`）从 `infrastructure/database/repositories` 平移，改 import `noesis.storage.postgres.models`；`kb_collection_config_repository` 新建（对齐 `KbCollectionConfigService` DB 方法，含 `load_query_params_sync` 经 `pg_manager.get_sync_session`）；其余业务 repo 按需提取
- [ ] 1.6 `noesis_server/infrastructure/database/{engine,dependency,migrations}.py` 改为 re-export `noesis.storage`（过渡，阶段 4 删）：`get_db` 改 `async with pg_manager.get_async_session_context() as db: yield db`（72 处 Depends 签名不动）；`init_database` 委托 `pg_manager.initialize`；`Base`/`AsyncSessionLocal`/`inspector` re-export。验证行为一致
- [ ] 1.7 更新 `test_harness_package_boundary.py`：`FORBIDDEN_PLATFORM_PACKAGES`/`LEGACY_PLATFORM_PACKAGES` 移除 `kb`；新增 `noesis.storage/**`/`noesis.repositories/**` 不得 import `noesis_server.*` 断言（阶段 5 前可临时跳过并记原因）
- [ ] 1.8 确认 `noesis.config.checkpointer`（独立 checkpoint 库，psycopg 原生池）与 `pg_manager`（业务库 SQLAlchemy engine）并存不合并，checkpointer 不动；在 spec/design 声明边界

## 2. knowledge 引擎平移

- [ ] 2.1 `git mv noesis_server/kb/{document_parse,chunk,retrieval,rerank,embedding,deepdoc,_ragflow_compat,seed,download_models.py,README.md}` → `noesis/knowledge/{parser,chunking,retrieval,rerank,embedding,deepdoc,_ragflow_compat,seed,download_models.py,README.md}`
- [ ] 2.2 `noesis_server/kb/qdrant.py` → `noesis/knowledge/implementations/qdrant.py`：`QdrantKB(KnowledgeBase)`，模块级 `_qdrant_client`/`_is_connected`/`init_qdrant_client`/`close_qdrant_client` 收敛为 `KnowledgeBaseManager` 拥有的实例状态
- [ ] 2.3 新建 `noesis/knowledge/{base.py,factory.py,manager.py,runtime.py,schemas.py,read_models.py}`：`KnowledgeBase` ABC（仅现有调用面方法）+ domain exception（`KBNotFoundError`/`KBNameConflictError`/`QdrantNotConnectedError`/`KBOperationError`）+ `FileStatus`；`KnowledgeBaseFactory` 注册表；`KnowledgeBaseManager`（用 repository、own Qdrant 客户端、领域方法抛 domain exception）；`runtime.py` 单例 `knowledge_base` 并注册 Qdrant 实现
- [ ] 2.4 全仓 import 改写：`noesis_server.kb.*` → `noesis.knowledge.*`（子包改名映射 `document_parse`→`parser`、`chunk`→`chunking`）；含 `from noesis_server.kb.qdrant import ...` → `from noesis.knowledge.implementations.qdrant import ...`
- [ ] 2.5 重建 `noesis/knowledge/__init__.py` 懒加载门面（平移原 `kb/__init__.py` 导出），`__getattr__` 模式，顶层不 import `qdrant-client`/DeepDoc 重型模块
- [ ] 2.6 `grep -rn "noesis_server.kb"` 全仓清零

## 3. 领域逻辑并入 manager，删除 KB 注入

- [ ] 3.1 将 `noesis_server/services/knowledge_base_service.py` 领域方法（create/upload/search/delete/校验/`_require_collection_*`）迁入 `noesis.knowledge.manager`，抛 domain exception；HTTP 翻译留 API
- [ ] 3.2 将 `kb_collection_config_service.py` 的 DB 方法迁入 `noesis.repositories`（已在 1.5 起步，此处补齐并删除平台 service）；同步 `load_query_params_sync` 改经 `pg_manager.get_sync_session`
- [ ] 3.3 `noesis/tools/kb_search_tool.py` 改直接 import `noesis.knowledge`（`KbRetrievalService`/`normalize_query_execution_params`/Qdrant manager/`is_vlm_configured`），删除全部 `require_*` 调用
- [ ] 3.4 `noesis/runtime/deps.py`：删除 `bind_kb_services`/`bind_kb_retrieval`/`temporary_kb_runtime`/`require_kb_retrieval_service`/`require_qdrant_service`/`require_normalize_query_execution_params`/`require_is_qdrant_connected`/`require_kb_collection_config_service`/`require_is_vlm_configured`；`runtime/__init__.py` `_EXPORTS` 同步移除
- [ ] 3.5 `noesis_server/services/harness_wiring.py` 删除 `bind_kb_services`/`bind_kb_retrieval`/`bind_vlm` 中 KB 相关调用

## 4. 平台 data 层收敛与 API 薄化

- [ ] 4.1 `noesis_server/api/knowledge_base_api.py` 薄化：直调 `from noesis.knowledge.runtime import knowledge_base`，catch domain exception → HTTP（404/409/503/500），`ResponseUtil` 包装；路径 `/api/kb/*` 与字段不变
- [ ] 4.2 删除 `noesis_server/infrastructure/database/`（engine/dependency/migrations/repositories 全部已迁入 harness）；所有平台 service/API/middleware/bootstrap 改 import `noesis.storage`/`noesis.repositories`；`grep -rn "noesis_server.infrastructure.database"` 清零
- [ ] 4.3 删除 `noesis_server/models/`（全量 ORM 已迁入 `noesis.storage`）；删除 `noesis_server/kb/`、`services/knowledge_base_service.py`、`services/kb_collection_config_service.py`；`grep -rn "noesis_server.models"` 清零
- [ ] 4.4 `sql/{initialize_postgresql,rotate_registration_invite}.py`、`noesis_server/middleware/csrf.py`、`evals/kb/run.py` 改 import `noesis.storage`/`noesis.repositories`
- [ ] 4.5 平台其余 service（auth/chat/run/scheduled_task/settings/mcp/memory/user 等）改为经 `noesis.repositories` 做 DB 访问，删除内联 ORM/engine 用法；跨 repository 事务由薄编排 service 在 `async with pg_manager.get_async_session_context() as db:` 内构造多 repo 共享 session 统一 commit
- [ ] 4.6 `noesis_server/server.py` lifespan 改调 `pg_manager.initialize()`（含迁移）+ `init_checkpointer()` + `noesis.knowledge.runtime` 初始化；关闭补 `pg_manager.close()` + `close_checkpointer()`；`bootstrap/kb.py` 改 import `noesis.knowledge`

## 5. 评测、测试、文档与回归

- [ ] 5.1 `evals/bootstrap.py`、`evals/case/rag/{ingest,provider}.py`、`evals/kb/run.py` 改 import `noesis.knowledge`/`noesis.storage`/`noesis.repositories`；删除 `temporary_kb_runtime`，评测直接用 `noesis.knowledge.runtime` + `pg_manager`
- [ ] 5.2 更新测试 import 与 `@patch` 目标：约 18 个 `test_kb_*`/`test_document_parser`/`test_embedding_config`/`test_scene_rag_context`（`noesis_server.kb`→`noesis.knowledge`），约 9 个 DB/model 消费者测试（`test_auth_domain_boundary`/`test_automation_runs`/`test_batch_delete_sessions`/`test_chat_service_user_id`/`test_chat_session_*cleanup`/`test_run_api_contract`/`test_session_title_once`/`test_agent_run_repository`/`test_settings_*`/`integration/test_message_sequence_postgres`）的 `@patch("noesis_server.infrastructure.database.engine...")`/`@patch("noesis_server.models...")` → `noesis.storage`
- [ ] 5.3 启用 `test_harness_package_boundary.py` 新断言：`noesis.storage`/`noesis.repositories`/`noesis.knowledge` 不反向依赖平台；`noesis_server/kb`、`noesis_server/infrastructure/database`、`noesis_server/models` 不存在；engine/ORM/Alembic 唯一来源 `noesis.storage`；API 直调 harness 单例
- [ ] 5.4 `packages/harness/pyproject.toml` 新增 `qdrant-client`/`sqlalchemy>=2.0`/`asyncpg`/`alembic`；验证 `import noesis`/`import noesis.factory` 不触发 `noesis.knowledge`/`noesis.storage` 重型子模块
- [ ] 5.5 扩展 `test_built_wheel_imports_outside_backend`：wheel env 能 import `noesis.storage`/`noesis.knowledge`/`noesis.repositories` 门面，不要求 ONNX 依赖在场
- [ ] 5.6 跑 `backend/tests/` 全量回归（harness/runtime/stream/KB/tool failure/persistence/auth/chat/run/scheduled_task/settings）；执行一条 Harbor Agent E2E + 一条 Agentic RAG ingest 冒烟 + 一条线上 SuperAgent 流式冒烟，确认 SSE/落库/evidence identity/业务表读写/请求级事务语义不变
- [ ] 5.7 更新 `docs/architecture/knowledge-base.md` §2 分层图与禁令描述；新增 harness 数据层架构说明（`noesis.storage`/`noesis.repositories`，含 checkpointer 与 pg_manager 并存边界）；`docs/NOTES.md` DeepDoc vendor 路径同步；`noesis_server/kb/README.md` 迁为 `noesis/knowledge/README.md`
- [ ] 5.8 使用 `code-review` 按本 spec 与仓库规范审查；对确认存在的浅 wrapper/重复注入分支/旧兼容 import/双 engine 过渡残留使用 `code-simplification`
- [ ] 5.9 确认 OpenSpec 所有任务与规格场景可追溯；归档时与 `agent-harness`/`knowledge-base` 主规格对齐
