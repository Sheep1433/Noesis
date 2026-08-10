## 1. 已完成的核心包迁移

- [x] 1.1 将唯一 ORM `Base`、Postgres manager、全量 model 与 Alembic 迁入 `noesis.storage`
- [x] 1.2 将 auth / agent-run / settings / KB 配置 repository 迁入 `noesis.repositories`
- [x] 1.3 将 Knowledge 解析、分块、检索、rerank、embedding、Qdrant 与 DeepDoc 迁入 `noesis.knowledge`
- [x] 1.4 删除 KB runtime deps 注入面，Agent/评测直接调用 `noesis.knowledge`
- [x] 1.5 将平台 service/domain/schema 收入 `noesis`，宿主收敛为 `backend/server`
- [x] 1.6 将 Agent 组件收入 `noesis.agents`，保持 `noesis` 顶层子系统稳定
- [x] 1.7 保留 checkpointer 与业务 pg_manager 两套独立连接职责

## 2. YuXi 式边界收尾

- [x] 2.1 修订 proposal/design/spec：`noesis` 是核心后端包，`server` 是进程与 HTTP 边缘
- [x] 2.2 新增并接入 `KnowledgeBaseManager`，移除 Qdrant 模块级 client/connection 全局状态
- [x] 2.3 补齐核心包直接依赖与 wheel 门面隔离测试
- [x] 2.4 强化边界测试：禁止 `noesis → server`、`domain → agents`，验证 server 无第二套数据层
- [x] 2.5 将跨 Agent/交付共享的 `ContextMetricsRegistry` 移入 `runtime.observability`，消除 `domain → agents` 倒置
- [x] 2.6 将 workspace package 从 `packages/harness` / `noesis-harness` 改名为 `packages/noesis-core` / `noesis-core`，保持 `noesis.*` import 不变
- [x] 2.7 更新 backend、CLI、部署、评测和边界测试中的包路径与 dependency metadata

## 3. 文档与验证

- [x] 3.1 更新根 `AGENTS.md`、`backend/AGENTS.md` 与长期架构文档中的旧 `noesis_server`/纯 harness 表述
- [x] 3.2 运行 Alembic history、结构守卫、Knowledge、SSE、持久化与全量 backend tests
- [x] 3.3 使用 `code-review` 按规格与仓库规范审查；确认存在的复杂实现再使用 `code-simplification`
- [x] 3.4 确认 change spec 与实现可追溯，准备归档
