## Why

Noesis 已在 `chat.vue` 挂载 `TodoList` 组件并在 Pinia 中预留 `todos` 状态，但前端从未在流式路径上消费 `write_todos`。`create_deep_agent` 默认带 `TodoListMiddleware`，桥接层对 `write_todos` 已按既有协议发出 **`tool-input-available`**（`toolName` + `input.todos`），数据已在 SSE 中，缺的是 chat 页解析与 `update_todos` 接线。在**不新增 SSE 事件类型**的前提下补齐「工具输入 → TodoList」即可。

## What Changes

- **不新增** `todos-update` 或其它专用 SSE 类型；沿用现有 **`tool-input-available`** 帧。
- **前端** 在 `useSSEStream` 处理 `tool-input-available`（或 `chat.vue` 的 `onToolCall`）时：当 **`toolName === "write_todos"`** 且 `input.todos` 为数组时，校验并调用 **`businessStore.update_todos`**；`chat.vue` 现有 `TodoList` 绑定不变。
- 抽取可复用的 **`parseWriteTodosInput(input)`**（或等价工具函数），统一校验 `content` / `status` 枚举；空数组时清空面板。
- **展示范围**：凡流式对话中出现 `write_todos` 的 Agent（不限 `qa_type`）均更新 Todo 面板。
- **生命周期**：新会话、切换会话、流结束清理时清空 `todos`；**不持久化**、刷新或重进历史**不恢复**面板（消息内 tool part 仍可回看）。
- **深度研究 prompt（可选）**：引导多步任务调用 `write_todos`，提高面板出现概率。
- **文档**：在 `docs/prd/platform/SSE流式数据设计.md` 补充「`write_todos` 与 TodoList 的消费约定」（非新事件，为前端语义说明）。
- **非目标**：`LangGraphSseBridge` 改动；`todos-update` 事件；`plan_mode` / 完成前拦截；`agent_state`；TestAssistant `phase-*`；todo 落库。

## Capabilities

### New Capabilities

- （无）归入既有 `chat-sessions-and-streaming`。

### Modified Capabilities

- `chat-sessions-and-streaming`：补充 chat 页对 **`tool-input-available` + `write_todos`** 的消费语义、TodoList 展示与生命周期边界；**不**修改 SSE 事件类型表。

## Impact

- **后端**：无桥接层改动；可选 `backend/agent/deep_research_agent.py`（prompt）。
- **前端**：`frontend/src/views/chat/useSSEStream.ts`（或 `chat.vue`）、可选 `frontend/src/utils/writeTodos.ts`、 `chat.vue` 清空逻辑、`TodoList`（可选样式）。
- **文档**：`docs/prd/platform/SSE流式数据设计.md`（消费约定附录）、`docs/test/test_tdd_design.md`（前端解析测试点）。
- **测试**：以前端单元/组件测试或轻量集成测试为主；**不**要求桥接层 `todos-update` golden。
- **兼容性**：**非 BREAKING**；仅新增前端行为，协议不变。
