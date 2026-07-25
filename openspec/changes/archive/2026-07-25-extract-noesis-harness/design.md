## Context

Delivery（`domain/chat/delivery`）已独立；评测仍绕开或半绕开 Agent 装配。DeerFlow 将内核放在 `packages/harness`，Gateway 只做 HTTP；Client 与 Gateway **共用 factory，不共用投递管线**。

## Goals / Non-Goals

**Goals**

- 发行包目录 `backend/packages/harness`；Python 包目录 `backend/packages/harness/noesis`（import `noesis`）
- `noesis -X→ services/domain/models/api/kb/config/common`、`-X→ domain.chat.delivery`
- LLM 实现位于 `noesis.llm`，所有具体 Agent 位于 `noesis.agents`
- `noesis.runtime` 负责执行期横切能力：stream、HITL、宿主依赖端口、附件输入适配、logging
- 评测 / Harbor 经 `create_noesis_agent` + `stream_agent_events`
- 平台经 `noesis_server.services.harness_wiring` 单向绑定附件/KB
- 平台代码收敛到 `backend/noesis_server`，与 `packages/harness`、`evals` 形成三个清晰顶层边界

**Non-Goals**

- 不把 RunOrchestrator / PersistSink / SSE 迁入 harness
- 不发独立 PyPI wheel / 不拆第二进程
- 不重写 CaseCoordinator 业务

## Decisions

1. **目录名 `harness` / Python 包名 `noesis` / 发行名 `noesis-harness`**：对齐 DeerFlow 的 `packages/harness/deerflow` 分层，避免 `harness/harness` 语义重复。本期是 backend workspace package，不承诺独立 PyPI 发布。
2. **LLM 内聚到 `noesis.llm`**：LLM factory/catalog/model limits 是 Agent 装配核心，不是可独立运行的 sibling 产品；保持一个发行包和一个顶层命名空间。
3. **运行时基础设施内聚**：Agent 所需 env/yaml/path/MCP/checkpointer 位于 `noesis.config`，logging 位于 `noesis.runtime.logging`；平台数据库模块可依赖 noesis config，反向禁止。
4. **平台能力用 `noesis.runtime.deps` 绑定**：避免内核静态 import services；FastAPI lifespan 绑定完整平台能力，eval runtime 仅绑定 benchmark 明确需要的能力。
5. **tool_failure / HITL / sandbox lifecycle / skills revision 下沉 noesis**：仓内调用与测试迁至新路径，不保留 domain/services shim。
6. **Harbor**：ProxyHarborBackend 环境特殊，走 factory+stream，不强制完整 SuperAgent sandbox session，也不加载平台 services wiring。
7. **不保留旧命名空间 shim**：这是显式 breaking import migration；单一 `noesis.*` 权威路径避免遗漏被兼容层掩盖。
8. **边界由测试守护**：AST 扫描禁止上层包、backend config/common 与旧命名空间；隔离 wheel smoke test 验证 backend 目录外可导入 factory/LLM。
9. **顶层目录表达稳定子系统**：`agents` 统一承载具体 Agent；`runtime` 统一承载执行期适配。禁止重新出现顶层 `profiles`、`case_generate`、`attachments` 或散落的 `stream.py` / `hitl.py` / `deps.py`。
10. **公共调用经子系统门面**：宿主和评测可从 `noesis.config` / `noesis.runtime` 导入常用配置、路径、日志、stream 与依赖绑定函数；门面使用惰性导出，避免配置初始化和 logging 之间的循环依赖。细分模块仍是内部组织和精确 patch 的合法路径。
11. **平台使用单一顶层命名空间 `noesis_server`**：原 `api` / `services` / `domain` / `config` / `common` / `constants` / `exceptions` / `middleware` / `models` / `schemas` / `kb` 全部归入该目录，避免 backend 根目录暴露十余个可导入顶层包；旧路径不保留 shim。
12. **平台内部依赖仍须单向**：API → application services → domain/KB/harness；进程 bootstrap 可组装所有平台能力。Telegram polling 与默认 KB 初始化属于 bootstrap/application，不放在 domain/KB 核心中；KB 核心不 import application services。
13. **Knowledge Base API 保持薄层**：Qdrant、集合配置、上传暂存、解析入库与检索参数合并由 `noesis_server.services.knowledge_base_service` 编排；API 仅负责 FastAPI dependency、请求读取与 `ResponseUtil` 封装。

## Risks

| 风险 | 缓解 |
|------|------|
| 旧 `agent.*` / `harness.*` / `llm.*` 外部调用断裂 | breaking change 明示；仓内调用一次性迁至 `noesis.*` |
| deps 未绑定导致运行时错误 | server lifespan + evals bootstrap + tests/conftest 强制 wire |
| 规格与代码边界再次漂移 | AST 回归测试锁定 forbidden imports、目录层级与唯一顶层命名空间 |
| 配置文件不在 wheel 中 | `NOESIS_CONFIG_PATH` 显式指定；未指定且找不到 workspace config 时使用内置默认 |
| 平台命名空间迁移遗漏 import/patch | 全仓一次性 AST/文本扫描旧顶层路径；不保留兼容 package 掩盖遗漏 |

## Migration

见 `tasks.md`。分支：`feat/extract-noesis-harness`。
