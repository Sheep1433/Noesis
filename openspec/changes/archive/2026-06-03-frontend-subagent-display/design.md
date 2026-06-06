## Context

- **DeepAgents 委派**：`SubAgentMiddleware` 通过 `task` 工具委派子 Agent；input 含 `subagent_type`、`description`、`prompt`；output 以 `Task Succeeded. Result:` / `Task failed.` / `Task timed out` 等前缀标识终态。
- **事件冒泡（实测）**：`BaseAgent._stream_agent_response` 使用 `agent.astream_events(...)`，**子图内的 `on_tool_start` / `on_tool_end` 会冒泡到同一流**。`LangGraphSseBridge._handle_langchain` 对全部 tool 事件发 SSE 并写入 assistant `content.parts`，**未按 `parent_ids` 过滤**。
- **首版 UI 现状（已实现）**：`toolName === "task"` → `SubagentCollapse`；其它 tool → `ToolCallCollapse`。子 Agent 内部的 read/bash 等因此与 `task` 卡片**同级平铺**在主气泡时间线中，归属不清。
- **后端预留**：`t_chat_session.parent_id`、`GET /api/chat/sessions/{id}/children` 已存在，Agent 流式路径**未**在委派时写子会话；drill-down 为二期。
- **参考**：deer-flow 用 LangGraph SDK 多 message + `assistant:subagent` 分组 + `SubtaskCard`；Noesis 为单条 assistant 的 flat `parts`，用 **`parentTaskCallId`** 在 parts 上模拟分组。

## Goals / Non-Goals

**Goals:**

- `task` part 以 **SubagentCollapse** 展示委派元数据、状态、prompt、结果/错误。
- 子 Agent **内部 tool parts**（带 `parentTaskCallId`）在 **SubagentCollapse 展开区内**以 ToolCallCollapse 展示，**不**在主界面平铺。
- 主 Agent **顶层** tool（无 `parentTaskCallId`）仍在主界面 ToolCallCollapse 展示。
- 流式与历史共用 `content.parts`；提供 `parseTaskToolInput` / `parseTaskToolOutput`。
- 桥接层打标 + 契约测试；文档与 `test_tdd_design.md` 同步。

**Non-Goals:**

- 新增 `subagent-*` SSE 事件类型。
- 后端自动创建 `parent_id` 子会话、侧栏 drill-down（二期）。
- 首版嵌套阶段收编子 Agent 内部 **text/reasoning**（可后续用同一打标扩展）。
- 修改 DEEP_RESEARCH 禁用 subagent 的策略。
- 纯前端启发式归属（按 task 前后区间猜 tool 归属）——并行 task 易错，不采用。

## Decisions

### 1. 以 message parts 为单一数据源

不新增 Pinia `subtasks` store；流式走 `onToolCall` / `onToolResult` → parts；UI 从 parts 派生（含嵌套分组）。

### 2. `task` → SubagentCollapse；顶层 tool → ToolCallCollapse

```
part.type === 'tool' && part.toolName === 'task'
  → SubagentCollapse（含 childParts）
part.type === 'tool' && part.parentTaskCallId
  → 不进入主循环（由 SubagentCollapse 内渲染）
part.type === 'tool'
  → ToolCallCollapse
```

### 3. 状态派生（`parseTaskToolOutput`）

| 条件 | status |
|------|--------|
| `part.status === 'running'` 且无 output | `in_progress` |
| output 以 `Task Succeeded. Result:` 开头 | `completed`，result 为后缀 trim |
| output 以 `Task failed.` 开头 | `failed`，error 为后缀 trim |
| output 以 `Task timed out` 开头 | `failed`，error 为全文 |
| `part.status === 'error'` | `failed`，error 取 `part.error` 或 output |
| 其它非空 output | `in_progress`（中间态） |

### 4. 嵌套打标：`parentTaskCallId`（桥接层）

**选择**：在 `LangGraphSseBridge` 处理 `on_tool_start` / `on_tool_end` / `on_tool_error` 时：

- 维护 `ctx["run_id_to_tool_call_id"]` 与 `ctx["task_tool_call_stack"]`。
- `toolName === "task"` 时 `stack.push(toolCallId)`；`on_tool_end(task)` 时 `pop`。
- 其它 tool：用事件的 `parent_ids` + `run_id` 映射解析所属 `task` 的 `toolCallId`，写入 SSE payload 与 `ToolPart.parent_task_call_id`（JSON 键 **`parentTaskCallId`**）。

**理由**：

- 事件已在流中，打标比丢弃或新增 SSE 类型改动更小。
- 持久化后历史刷新可恢复嵌套结构。
- 旧消息无字段时前端降级平铺，兼容性好。

**未采纳**：

- 桥接层丢弃子 Agent tool（丢失卡片内可观测性）。
- 纯前端区间启发式（并行 task 会错绑）。

### 5. 前端分组：`buildDisplayParts`

按 `parentTaskCallId` 将 child parts 挂到对应 `task` part；`SubagentCollapse` 接收 `childParts` prop，展开区 `v-for` 渲染 `ToolCallCollapse`。

### 6. useSSEStream 与 chat 接线

`useSSEStream` 不需改事件类型解析；`upsertToolInputPart` / `applyToolOutput` 需透传 `parentTaskCallId`（若 SSE 帧携带）。

### 7. 子会话 API（二期，不接线）

`GET /api/chat/sessions/{id}/children` 待后端在 `task` 委派时写 `parent_id` 子会话后可做 drill-down；本 change 不调用。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| DeepAgents `parent_ids` 形状与预期不符 | 实现前打日志验证 FaultOperation 真流；单测 mock 多种 parent 链 |
| 并行多个 `task` | 栈 + run_id 映射到各自 toolCallId |
| 历史无 `parentTaskCallId` | 前端降级平铺，与首版行为一致 |
| DeepAgents output 前缀变更 | 常量单点 + parse 测试 |
| 子 Agent text 仍可能混入主时间线 | 嵌套阶段仅收 tool；text 打标列为后续增量 |

## Migration Plan

1. ~~首版：`parseTaskTool.ts` + `SubagentCollapse` + `chat.vue` 分支~~（已完成）
2. 桥接层：`parentTaskCallId` 打标 + `message_builder` 字段 + contract test
3. 前端：`messageParts` 字段 + `buildDisplayParts` + `SubagentCollapse` child 渲染
4. 更新 PRD / test_tdd_design；FaultOperation 冒烟：主界面无泄漏、卡片内可见内部 tool
5. 回滚：停止写 `parentTaskCallId` 即可恢复平铺；首版 SubagentCollapse 仍可用

## Open Questions

- （实现嵌套前）用一次 FaultOperation 真流确认 `astream_events` 项上 `parent_ids` 与 `task` run 的对应关系，微调 `_resolve_parent_task_call_id` 兜底规则。
