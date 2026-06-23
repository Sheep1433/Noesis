## Why

Noesis chat 页已能展示**单条 assistant 回复**的累计 LLM 计费 token（`usage-update` / `↑input ↓output`），但用户无法在发消息前感知**会话上下文窗口**还剩多少余量。后端 `SummarizationOffloadMiddleware` 在每次 `before_model` 已用 `token_counter(messages)` 估算上下文占用，却未通过 SSE 推送、未持久化、未在 UI 展示。长对话、工具大结果、子 Agent 嵌套等场景下，用户缺少类似 Cursor Composer 的上下文健康指示，不利于成本与稳定性预期。

## What Changes

- **配置**：新增独立 `context` 配置段（`max_input_tokens`、`display_enabled`），作为展示与摘要/卸载的**统一上下文窗口上限**；废弃 `summarization.max_input_tokens` 回退到 `generation.max_tokens` 的语义混淆（配置迁移见 design）。
- **后端中间件**：新增 `ContextMetricsMiddleware`（独立于 `summarization_enabled`），在每次 `before_model` 统计消息列表 token 估算，经 LangGraph custom stream 发出指标。
- **SSE**：新增 **`context-update`** 事件，负载含 `current_tokens`、`max_tokens`、`used_percentage`；摘要/工具卸载导致上下文骤降时再次推送。
- **桥接层**：`LangGraphSseBridge` 将 custom stream / 等价事件映射为 `context-update` SSE 帧；golden 测试扩展。
- **持久化**：会话级最近上下文快照写入 `session.extra.context`（或等价字段），历史加载时可恢复指示器。
- **前端**：在 `chat.vue` 输入区 footer（composer 下方）展示 **环形进度 + 百分比**；**hover tooltip** 显示绝对值 `87K / 128K`（复用 `formatTokenCount`）；与现有 assistant 气泡内 usage 行**分开**，不合并。
- **范围**：覆盖经 `create_noesis_agent` 的 `COMMON_QA`、`FAULT_OPERATION_QA`、`DEEP_RESEARCH_QA`；`TEST_CASE_QA`（CaseCoordinator StateGraph）首期标为不支持或隐藏指示器。
- **非目标**：子 Agent 独立上下文明细 UI、费用估算、前端本地 tiktoken 重算、替换现有 `usage-update` 语义。

## Capabilities

### New Capabilities

（无独立新 capability；行为归入平台聊天规格。）

### Modified Capabilities

- `platform-chat`：新增 `context-update` SSE 契约、会话上下文快照持久化、chat 页 composer footer 上下文指示器（百分比 + hover 绝对值）、可配置 `context.max_input_tokens`。

## Impact

- **后端**：`backend/config/yaml_config.py`、`backend/config/env.py`、`backend/config.example.yaml`、`backend/agent/middlewares/`（新 `context_metrics_middleware.py`）、`backend/agent/factory.py`、`backend/agent/middlewares/summary_offload_middleware.py`（共享 max 配置）、`backend/utils/langgraph_sse_bridge.py`、`backend/services/qa_service.py`、会话 ORM/extra 字段（若需迁移）。
- **前端**：`frontend/src/views/chat/useSSEStream.ts`、`frontend/src/views/chat.vue`、可选新组件 `ContextWindowIndicator.vue`、`frontend/src/views/chat/messageParts.ts`（格式化复用）。
- **测试**：`backend/tests/test_langgraph_sse_bridge_contract.py`、中间件单测、前端类型/解析测试（按仓库惯例）。
- **文档**：`docs/prd/platform/SSE流式数据设计.md`（若存在）补充 `context-update`。
- **API 兼容**：新增 SSE 事件与可选 session extra 字段；**不破坏**现有 `usage-update` / `finish.usage` 消费者（未知事件忽略策略不变）。
