## Context

- **现状**：桥接层已对 `write_todos` 发出 `tool-input-start` / `tool-input-available`（`toolName`、`input.todos`）。`useSSEStream` 在 `tool-input-available` 时调用 `onToolCall(name, input, id)`，但 `chat.vue` 未识别 `write_todos`。Pinia `update_todos` 存在且未被调用。
- **方案选型（已确认 B）**：不新增 `todos-update`；前端在既有 `tool-input-available` 路径更新 Todo 面板。相对方案 A（专用事件），B 更少改动、不扩展 SSE 契约，代价是前端耦合工具名 `write_todos` 与 `WriteTodosInput` 形状。
- **产品决策**：全 Agent 展示；不恢复历史；不做 plan_mode；TestAssistant 仍用 `phase-*`。

## Goals / Non-Goals

**Goals:**

- `tool-input-available` 且 `toolName === "write_todos"` 时，TodoList 展示最新 `input.todos` 快照。
- 校验与防御性解析，避免非法结构导致 UI 异常。
- 会话级清空与「不持久化面板」边界明确。
- （可选）深度研究 prompt 引导 `write_todos`。

**Non-Goals:**

- 新增 SSE 事件、`langgraph_sse_bridge.py` 修改。
- todo 落库、checkpoint 恢复、deer-flow TodoMiddleware。
- TestAssistant 阶段条改造。

## Decisions

### 1. 主路径：在 `tool-input-available` 分支更新 todos（方案 B）

**选择**：在 `useSSEStream` 的 `tool-input-available` 处理中，解析 `toolName` 与 `input` 后，若 `name === 'write_todos'`，调用 `onWriteTodos?.(todos)`；`chat.vue` 实现为 `businessStore.update_todos(parsed)`。

**理由**：
- 数据已在 SSE 中，无需重复推送。
- 与 `onToolCall` 用于 message `parts` 的路径可并存：先 `onToolCall` 写 part，再 `onWriteTodos` 更新面板（或仅在 `chat.vue` 的 `onToolCall` 内分支，二选一实现，避免双份逻辑）。

**推荐实现形态**（择一，tasks 中落实）：

```
tool-input-available
  → onToolCall (现有，写 tool part)
  → chat.vue onToolCall 内: if write_todos → parseWriteTodosInput → update_todos
```

或：

```
tool-input-available
  → onToolCall
  → useSSEStream 内 if write_todos → onWriteTodos (专用回调)
```

**`parseWriteTodosInput` 规则**：

- 输入：`input.todos` 为数组；每项 `content: string`、`status ∈ { pending, in_progress, completed }`。
- 非法项跳过；若过滤后为空且原始为 `[]`，则 `update_todos([])`。
- 非数组或缺失 `todos`：不更新 store（保持上一快照）。

### 2. 不采用 `todos-update`（方案 A，已否决）

**未采纳理由**：当前仅需从既有 tool 帧驱动 UI；新增事件增加文档、桥接测试与双通道维护成本，收益有限。若未来从 graph `values.todos` 推送且不经 `write_todos` 工具，再评估专用事件或 `agent_state`。

### 3. 与 message parts / ToolCallCollapse

`write_todos` **继续**走现有 `onToolCall` → `upsertToolInputPart`，消息持久化与折叠块不变。TodoList 为**会话级 ephemeral 状态**，与 parts 解耦；刷新后不根据 parts 重建面板（产品已确认）。

### 4. UI 与生命周期

- 沿用 `TodoList` + `todo-list-wrapper`；`todos.length === 0` 隐藏。
- 清空时机：`chat.vue` 已有新会话清理；确认切换会话、`onFinish`/`onError` 时 `todos = []`。

### 5. 深度研究 prompt（可选）

`DeepResearchAgent` system prompt 简短要求：≥3 步任务使用 `write_todos`，步骤与 Skill 子问题对齐。

### 6. 与 `phase-*` 的关系

TestAssistant 用 `phase-*` + 阶段条；chat 用 `write_todos` + TodoList。互不替代。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 前端耦合 `write_todos` 工具名 | 常量单点定义；文档写明约定 |
| 模型不调工具，面板为空 | 可选 prompt |
| 面板与 ToolCallCollapse 重复 | 首版接受 |
| 非法 input | `parseWriteTodosInput` 过滤 |

## Migration Plan

1. 实现前端解析 + `chat.vue` 接线 + 清空逻辑。
2. 文档补充消费约定（非新事件）。
3. 可选 prompt；`pnpm lint` + 手动冒烟。
4. 回滚：移除前端分支即可，协议无变化。

## Open Questions

- （无）是否在首版调整 TodoList 为顺序列表展示——实现时按视觉成本决定。
