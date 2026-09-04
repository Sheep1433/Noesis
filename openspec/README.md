# OpenSpec 导航

主规格：`openspec/specs/<capability>/spec.md`。变更 delta：`openspec/changes/<name>/specs/`，归档后并入主规格。

**读 spec 以本目录为准**；`changes/archive/` 只作历史决策链。本目录约定见 `openspec/AGENTS.md`。

## 能力目录（16）

| 域 | 能力 id | 一句话 |
|----|---------|--------|
| **聊天平台** | `platform-chat` | 会话/消息、SSE 契约、落库状态机、qa 路由、流式 UI、引用与来源展示 |
| | `chat-composer` | 对话面生命周期、发送上传、附件、mentions、上下文面板 |
| **Agent** | `agent-runtime` | `.noesis/users` 布局、`/workspace` 坐标系、沙箱、Skills、记忆路由、web 工具、执行 Lifecycle、Token 可观测 |
| | `agent-harness` | 包边界与依赖方向（`noesis` 内核包 / `server` HTTP 壳单向依赖） |
| | `agent-profiles` | COMMON / SUPER / FAULT / TEST_CASE 四场景 |
| | `agent-hitl` | 审批策略、ask_user、多端 resume |
| | `agent-memory` | md 文件记忆层：五类条目、水位抽取、AutoDream 整理、主动召回 |
| | `agent-background-tasks` | 后台子 Agent 任务：start_task 同异步、followup、协作停止、完成通知 |
| | `agent-tool-failure-handling` | 工具调用/执行双层语义与权威生命周期 state |
| | `agent-delivery` | RunEvent 总线、Run 身份/状态机/恢复、PersistSink、SseDelivery、ChannelAdapter、Telegram/飞书运行时 |
| **知识库** | `knowledge-base` | API、解析、分块、检索、kb 评测指针 |
| **用户与部署** | `user-platform` | Session Cookie、Auth 域隔离、MCP 配置、PostgreSQL |
| | `user-settings` | 设置壳/section 注册表、画像与记忆编辑、定时任务/自动化、通道配置面、模型目录、通知、依赖诊断、导入导出/审计 |
| | `container-deployment` | Compose、Nginx、sandbox-runner、advisory lock 单实例 |
| **评测** | `offline-evals` | `evals.agent`（含记忆应召回）/ `case` / `compression` / `kb` |
| **仓库协作** | `repo-collaboration` | 开发纪律 skill 归属、审查经济学、决策记录体系、CI 链接/格式校验 |

> 能力演进史：2026-07-23 由约 33 份能力合并精简至 13；2026-08-10 归档碎片并入主规格；2026-09-03 治理轮补录 `agent-memory`（原 `agent-memory-cortex`，md 记忆层现行规格）、`agent-background-tasks` 与 `repo-collaboration` 三个未登记能力并同步归档 unify-run-delivery / research-source-provenance / repo-constraint-centralization 的 delta。旧 id（如 `agent-sandbox`、`user-auth`、`chat-kb-sources`）已并入上表，细节以 archive change 为准。

## 活跃变更（`openspec/changes/`，非 archive）

| Change | 说明 |
|--------|------|
| `tool-context-append-only` | 工具上下文 append-only 投影与预算收敛（23/24，剩用户人工验收） |
| `enable-distributed-sse-pubsub` | 跨实例 run 协调与分布式投递 |
| `ws-downlink` | WebSocket 下行通道 |
| `super-agent-research-harness` | 深度研究 harness（research-trace 证据链） |

## 推荐阅读顺序

| 目标 | 先读 | 再读 |
|------|------|------|
| 发消息 / SSE / 落库 | `platform-chat` | `agent-delivery` |
| Composer / 上传 / @ | `chat-composer` | `agent-runtime`（路径） |
| 工作区 / 沙箱 / 执行 Lifecycle | `agent-runtime` | `agent-profiles` |
| 记忆 / 抽取 / 召回 | `agent-memory` | `user-settings`（编辑入口） |
| 子 Agent 后台任务 | `agent-background-tasks` | `agent-delivery`（投递契约） |
| Run 生命周期 / 通道 | `agent-delivery` | `agent-hitl`（通道 resume） |
| 某一种 qa_type | `agent-profiles` | 对应实现 `packages/noesis-core/src/noesis/agents/` |
| 知识库 | `knowledge-base` | `docs/engineering/knowledge-base.md` |
| 设置 / 自动化 / 通道配置 | `user-settings` | `agent-delivery`（通道运行时） |
| 登录 / DB / Auth 边界 | `user-platform` | `container-deployment` |
| 包边界 / import 规则 | `agent-harness` | `offline-evals`（驱动方） |
| 协作流程 / 决策记录 | `repo-collaboration` | 根 `AGENTS.md` |

## qa_type 路由

| `qa_type` | 见 |
|-----------|-----|
| `COMMON_QA` | `agent-profiles` § COMMON |
| `FAULT_OPERATION_QA` | `agent-profiles` § FAULT |
| `TEST_CASE_QA` | `agent-profiles` § TEST_CASE |
| `SUPER_AGENT_QA` | `agent-profiles` § SUPER + `agent-hitl` + `agent-background-tasks` |

## 与代码对齐

内核与业务层统一位于 `backend/packages/noesis-core`（顶层包 `noesis`，distribution `noesis-core`）；HTTP 壳位于 `backend/server`（import `server.*`）。

| OpenSpec | 代码入口 |
|----------|----------|
| `platform-chat` | `noesis/chat/`（delivery / runs / event_mapping）、`noesis/services/qa/`、`frontend/src/views/chat/` |
| `chat-composer` | `noesis/services/mention_resolve_service.py`、`SessionContextPanel` |
| `agent-runtime` | `noesis/config/`、`noesis/agents/backends/`、`noesis/agents/middlewares/` |
| `agent-harness` | `packages/noesis-core`（`noesis` 包）+ `backend/server`；边界测试 `backend/tests/test_core_package_boundary.py` |
| `agent-profiles` | `noesis/agents/`（含 `case_generate/`） |
| `agent-hitl` | `noesis/agents/guardrails/`、`noesis/chat/hitl/` |
| `agent-memory` | `noesis/services/memory/`、`noesis/agents/tools/memory_tools.py` |
| `agent-background-tasks` | `noesis/agents/subagents/`（executor）、`noesis/services/bg_continuation_service.py` |
| `agent-tool-failure-handling` | `noesis/errors/tool_failure.py`、`noesis/chat/tool_state.py`、`noesis/chat/event_mapping/failure_notice.py` |
| `agent-delivery` | `noesis/chat/delivery/`、`noesis/chat/runs/`、`noesis/services/run_service.py`、`noesis/services/channel_run_service.py` |
| `knowledge-base` | `noesis/knowledge/`、`server/api/knowledge_base_api.py` |
| `user-platform` | `server/api/`（auth 等）、`noesis/auth/`、`noesis/repositories/`、Alembic/PostgreSQL |
| `user-settings` | `frontend/src/views/settings/`、`server/api/user_settings_api.py`、`noesis/services/scheduled_task_service.py` |
| `offline-evals` | `backend/evals/` |
| `container-deployment` | `deploy/` |
| `repo-collaboration` | `.agents/skills/`、`scripts/change-scope.py`、`docs/decisions/`、CI gate 脚本 |
