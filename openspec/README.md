# OpenSpec 导航

主规格：`openspec/specs/<capability>/spec.md`。变更 delta：`openspec/changes/<name>/specs/`，归档后并入主规格。

**读 spec 以本目录为准**；`changes/archive/` 只作历史决策链。

## 能力目录（11）

| 域 | 能力 id | 一句话 |
|----|---------|--------|
| **聊天平台** | `platform-chat` | 会话/消息、SSE 契约、落库状态机、qa 路由、流式 UI |
| | `chat-composer` | 对话面生命周期、发送上传、附件、mentions、上下文面板 |
| **Agent** | `agent-runtime` | `.data/users` 布局、`/workspace` 坐标系、沙箱、Skills、记忆、web 工具 |
| | `agent-profiles` | COMMON / SUPER / FAULT / TEST_CASE 四场景 |
| | `agent-hitl` | 审批策略、ask_user、多端 resume |
| | `agent-tool-failure-handling` | 工具调用/执行双层语义与 SSE 字段 |
| | `agent-delivery` | RunEvent 总线、PersistSink、SseDelivery、ChannelAdapter |
| **知识库** | `knowledge-base` | API、解析、分块、检索、kb 评测指针 |
| **用户与部署** | `user-platform` | Session Cookie、MCP 配置、PostgreSQL |
| | `container-deployment` | Compose、Nginx、sandbox-runner |
| **评测** | `offline-evals` | `evals.agent` / `case` / `compression` / `kb` |

> 2026-07-23 起由约 33 份能力合并精简；旧 id（如 `agent-sandbox`、`agent-runtime-paths`、`user-auth`）已并入上表，细节以 archive change 为准。

## 活跃变更（`openspec/changes/`，非 archive）

| Change | 说明 |
|--------|------|
| `unify-run-delivery` | Delivery Fan-out（主规格已吸收为 `agent-delivery`，change 可继续收尾任务） |
| `add-telegram-channel-adapter` | Telegram 真收发（并入 `agent-delivery`） |
| `add-agent-user-settings` | 设置面：记忆/通道/定时任务配置 |
| `kb-multimodal-retrieval` | 多模态检索调研 |
| `add-kb-citation-sources` | KB 引用角标 |
| `refine-tool-outcome-handling` | outcome 与前端展示收尾 |
| `fault-operation-agent-experience-learning` | 故障经验沉淀 |

## 推荐阅读顺序

| 目标 | 先读 | 再读 |
|------|------|------|
| 发消息 / SSE / 落库 | `platform-chat` | `agent-delivery` |
| Composer / 上传 / @ | `chat-composer` | `agent-runtime`（路径） |
| 工作区 / 沙箱 / 记忆 | `agent-runtime` | `agent-profiles` |
| HITL | `agent-hitl` | `agent-delivery`（通道 resume） |
| 某一种 qa_type | `agent-profiles` | 对应实现 `agent/profiles/` |
| 知识库 | `knowledge-base` | `docs/prd/knowledge-base/` |
| 登录 / DB | `user-platform` | `container-deployment` |

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
| `platform-chat` | `domain/chat/`、`services/qa/`、`frontend/src/views/chat/` |
| `chat-composer` | `services/mention_resolve_service.py`、`SessionContextPanel` |
| `agent-runtime` | `agent/backends/`、`config/user_data_paths.py` |
| `agent-profiles` | `agent/profiles/`、`agent/case_generate/` |
| `agent-hitl` | `agent/guardrails/`、`domain/chat/hitl/` |
| `agent-delivery` | `domain/chat/delivery/` |
| `knowledge-base` | `backend/kb/`、`api/knowledge_base_api.py` |
| `user-platform` | `api` auth、MCP、Alembic/PostgreSQL |
| `offline-evals` | `backend/evals/` |
| `container-deployment` | `deploy/` |
