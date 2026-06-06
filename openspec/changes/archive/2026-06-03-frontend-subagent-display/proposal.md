## Why

Noesis 后端已通过 DeepAgents `SubAgentMiddleware` 支持 `task` 工具委派子 Agent，数据库与会话 API 也预留了 `parent_id` 与子会话列表（`GET /api/chat/sessions/{id}/children`），但前端 chat 页原先把 `task` 当作普通工具折叠块展示，用户无法感知「主 Agent 委派了哪个子 Agent、任务进行到哪一步、子任务结果如何」。

**首版（已实现）** 已落地 `SubagentCollapse` 与 `parseTaskTool*` 解析，但验收中发现：`agent.astream_events` 会把**子 Agent 内部的 tool 事件**一并冒泡到主流，`LangGraphSseBridge` 未区分层级，导致 read/bash 等以 **ToolCallCollapse 平铺在主界面**，与 `SubagentCollapse` 卡片并列，用户难以区分归属。

因此需要在**不新增 SSE 事件类型**的前提下，通过桥接层 **`parentTaskCallId` 打标** + 前端 **嵌套渲染**，把子 Agent 内部 tool 收进对应 `SubagentCollapse，主 Agent 自身 tool 仍平铺展示。

## What Changes

### 首版（已完成）

- **不新增** SSE 事件类型；沿用 **`tool-input-start` / `tool-input-available` / `tool-output-available`**，当 `toolName === "task"` 时驱动 SubagentCollapse。
- **前端** 新增 **`SubagentCollapse`**：展示 `subagent_type`、`description`、运行状态、prompt、结果/错误摘要；样式对齐 `ToolCallCollapse` light 模式。
- **解析 util** `parseTaskToolInput` / `parseTaskToolOutput` / `shouldRenderSubagentPart`。
- **流式与历史** 共用 `content.parts` 渲染；不新增 Pinia subtasks store。

### 嵌套打标（待实现，本 change 延续）

- **桥接层** `LangGraphSseBridge`：根据 `astream_events` 的 `run_id` / `parent_ids` 与 `task` 工具 `toolCallId` 栈，为子 Agent 内部 tool 的 SSE 帧与落库 `ToolPart` 写入可选字段 **`parentTaskCallId`**（指向外层 `task` 的 `toolCallId`）；主 Agent 顶层 tool 该字段为空。
- **数据模型** `message_builder.py` / `messageParts.ts`：`ToolPart` / `ToolUiPart` 增加 `parentTaskCallId`（及 snake_case 别名）；`normalizeApiContent` 兼容读取。
- **前端** `buildDisplayParts`（或等价）：主循环跳过带 `parentTaskCallId` 的 part；`SubagentCollapse` 展开区渲染其 **child tool parts**（复用 `ToolCallCollapse`）。
- **向后兼容**：历史消息无 `parentTaskCallId` 时，行为与首版一致（内部 tool 仍平铺，不报错）。

### 不变 / 非目标

- **不新增** `subagent-*` 专用 SSE 事件类型。
- **子会话 drill-down**（`parent_id` 子会话 + `getSessionChildren`）仍为二期，本 change 不接线。
- **子 Agent 内部 LLM 正文/reasoning** 首版嵌套阶段可不收进卡片（仅收 tool）；后续可按同样打标扩展。
- **DEEP_RESEARCH** 禁用 subagent 的行为不变。

## Capabilities

### New Capabilities

- （无）归入既有 `chat-sessions-and-streaming`。

### Modified Capabilities

- `chat-sessions-and-streaming`：补充 `task` SubagentCollapse 语义；补充 **`parentTaskCallId` 嵌套 tool 展示**；**不**修改 SSE 事件类型表（仅在既有 tool 帧 JSON 上增加可选键）。

## Impact

- **后端**：`backend/utils/langgraph_sse_bridge.py`、`backend/utils/message_builder.py`；`test_langgraph_sse_bridge_contract.py` 补充 parent 打标用例。
- **前端**：`messageParts.ts`、`chat.vue`、`SubagentCollapse`、可选 `groupAssistantParts.ts`；`useSSEStream` 透传新字段即可，协议解析不变。
- **API**：复用既有会话/消息 API；可选字段写入 `content.parts` JSON。
- **文档**：`docs/prd/platform/SSE流式数据设计.md`、`docs/test/test_tdd_design.md`、本 change 下 proposal/design/spec/tasks。
- **兼容性**：**非 BREAKING**；旧 parts 无 `parentTaskCallId` 时前端降级为平铺。
