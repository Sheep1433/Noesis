# OpenSpec 导航

主规格：`openspec/specs/<capability>/spec.md`。变更 delta：`openspec/changes/<name>/specs/`，归档后并入主规格。

**读 spec 以本目录为准**；`changes/archive/` 只作历史决策链。

## 能力目录（13）

| 域 | 能力 id | 一句话 |
|----|---------|--------|
| **聊天平台** | `platform-chat` | 会话/消息、SSE 契约、落库状态机、qa 路由、流式 UI、引用与来源展示 |
| | `chat-composer` | 对话面生命周期、发送上传、附件、mentions、上下文面板 |
| **Agent** | `agent-runtime` | `.noesis/users` 布局、`/workspace` 坐标系、沙箱、Skills、记忆、web 工具、执行 Lifecycle、Token 可观测 |
| | `agent-harness` | harness 包边界与依赖隔离（noesis 包不反向依赖平台） |
| | `agent-profiles` | COMMON / SUPER / FAULT / TEST_CASE 四场景 |
| | `agent-hitl` | 审批策略、ask_user、多端 resume |
| | `agent-tool-failure-handling` | 工具调用/执行双层语义与 SSE 字段 |
| | `agent-delivery` | RunEvent 总线、Run 身份/状态机/恢复、PersistSink、SseDelivery、ChannelAdapter、Telegram/飞书运行时 |
| **知识库** | `knowledge-base` | API、解析、分块、检索、kb 评测指针 |
| **用户与部署** | `user-platform` | Session Cookie、Auth 域隔离、MCP 配置、PostgreSQL |
| | `user-settings` | 设置壳/section 注册表、画像与记忆编辑、定时任务/自动化、通道配置面、模型目录、通知、依赖诊断、导入导出/审计 |
| | `container-deployment` | Compose、Nginx、sandbox-runner |
| **评测** | `offline-evals` | `evals.agent` / `case` / `compression` / `kb` |

> 2026-07-23 起由约 33 份能力合并精简至 13；2026-08-10 进一步将 20 个归档碎片（含 `agent-run-delivery` 重复、10 个设置碎片、通道运行时等）并入主规格。旧 id（如 `agent-sandbox`、`agent-runtime-paths`、`user-auth`、`chat-kb-sources`、`settings-control-plane`）已并入上表，细节以 archive change 为准。

## 活跃变更（`openspec/changes/`，非 archive）

| Change | 说明 |
|--------|------|
| `sink-data-layer-into-harness` | 将业务 service、数据访问、知识库与交付运行时下沉到 harness，减少 DI 与转发 |
| `improve-knowledge-base-workbench` | 知识库列表/集合/分片/检索调试调整为连续检查工作台 |
| `kb-multimodal-retrieval` | 图表/架构图等多模态检索调研 |
| `mobile-chat-focus` | 移动端聊天聚焦，按任务意图收敛入口 |
| `retire-default-test-kb-collections` | 停止启动时隐式创建 `requirement_docs` / `test_case_docs` |

## 推荐阅读顺序

| 目标 | 先读 | 再读 |
|------|------|------|
| 发消息 / SSE / 落库 | `platform-chat` | `agent-delivery` |
| Composer / 上传 / @ | `chat-composer` | `agent-runtime`（路径） |
| 工作区 / 沙箱 / 记忆 / 执行 Lifecycle | `agent-runtime` | `agent-profiles` |
| Run 生命周期 / 通道 | `agent-delivery` | `agent-hitl`（通道 resume） |
| HITL | `agent-hitl` | `agent-delivery`（通道 resume） |
| 某一种 qa_type | `agent-profiles` | 对应实现 `packages/noesis-core/src/noesis/agents/` |
| 知识库 | `knowledge-base` | `docs/architecture/knowledge-base.md` |
| 设置 / 自动化 / 通道配置 | `user-settings` | `agent-delivery`（通道运行时） |
| 登录 / DB / Auth 边界 | `user-platform` | `container-deployment` |
| harness 包边界 | `agent-harness` | `offline-evals`（驱动方） |

## qa_type 路由

| `qa_type` | 见 |
|-----------|-----|
| `COMMON_QA` | `agent-profiles` § COMMON |
| `FAULT_OPERATION_QA` | `agent-profiles` § FAULT |
| `TEST_CASE_QA` | `agent-profiles` § TEST_CASE |
| `SUPER_AGENT_QA` | `agent-profiles` § SUPER + `agent-hitl` |

## 与代码对齐

| OpenSpec | 代码入口 |
|----------|----------|
| `platform-chat` | `noesis_server/domain/chat/`、`noesis_server/services/qa/`、`frontend/src/views/chat/` |
| `chat-composer` | `noesis_server/services/mention_resolve_service.py`、`SessionContextPanel` |
| `agent-runtime` | `packages/noesis-core/src/noesis/agents/backends/`、`packages/noesis-core/src/noesis/config/user_data_paths.py`、`packages/noesis-core/src/noesis/agents/middlewares/` |
| `agent-harness` | `packages/noesis-core/src/noesis/`；公共入口 `noesis.config` / `noesis.runtime` |
| `agent-profiles` | `packages/noesis-core/src/noesis/agents/`、`agents/case_generate/` |
| `agent-hitl` | `packages/noesis-core/src/noesis/agents/guardrails/`、`noesis.domain.chat.hitl` |
| `agent-delivery` | `noesis_server/domain/chat/delivery/`、`domain/chat/run/`、`services/channel_run_service.py` |
| `knowledge-base` | `backend/noesis_server/kb/`、`noesis_server/api/knowledge_base_api.py` |
| `user-platform` | `noesis_server/api` auth、MCP、`noesis_server/domain/auth/`、Alembic/PostgreSQL |
| `user-settings` | `frontend/src/views/settings/`、`noesis_server/api/user_settings_api.py`、`services/scheduled_task_service.py` |
| `offline-evals` | `backend/evals/` |
| `container-deployment` | `deploy/` |
