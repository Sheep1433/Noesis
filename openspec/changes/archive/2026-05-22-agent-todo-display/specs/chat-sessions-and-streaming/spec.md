## ADDED Requirements

### Requirement: chat 页 SHALL 从 write_todos 的 tool-input-available 更新 TodoList

在 `chat.vue` 流式对话路径中，当前端通过 `useSSEStream` 收到 SSE 帧 **`tool-input-available`**，且该帧的 **`toolName`**（或同义字段）为 **`write_todos`** 时，前端 SHALL 从帧载荷 **`input.todos`** 解析 todo 列表，并写入 Pinia **`businessStore.todos`**（或 `update_todos` action），驱动输入框上方 **`TodoList`** 组件。

解析规则 SHALL 为：

- **`input.todos`** 为数组时，逐项保留满足 **`content`**（string）且 **`status`** 为 `pending` | `in_progress` | `completed` 的项；非法项 SHALL 跳过。
- **`input.todos`** 为空数组时，前端 SHALL 调用 `update_todos([])`，Todo 面板 SHALL 隐藏。
- **`input.todos`** 缺失或非数组时，前端 SHALL NOT 更新 `businessStore.todos`（保持上一快照）。

本 Requirement **不** 要求服务端新增 SSE 事件类型；**依赖** 既有 `tool-input-start` / `tool-input-available` 协议。适用于流式路径上调用 **`write_todos`** 的所有 Agent，**不限** `qa_type`。

`write_todos` 的工具输入 SHALL 仍按既有规则进入 assistant **`content.parts`** 的 tool part；Todo 面板状态 **SHALL NOT** 写入 MySQL，且 **SHALL NOT** 从持久化消息或 checkpoint 恢复。

#### Scenario: 流式过程中面板随 write_todos 更新

- **WHEN** 用户于 chat 页发起流式对话，且 SSE 序列中包含 `tool-input-available`，`toolName` 为 `write_todos`，`input.todos` 含 `{ "content": "步骤一", "status": "in_progress" }`
- **THEN** TodoList SHALL 展示该条 todo 且状态为进行中

- **WHEN** 同一会话后续再次收到 `write_todos` 的 `tool-input-available`，其中某条 todo 的 `status` 变为 `completed`
- **THEN** TodoList SHALL 展示最新快照中的已完成状态

#### Scenario: 模型清空 todo 列表

- **WHEN** 收到 `write_todos` 的 `tool-input-available` 且 `input.todos` 为 `[]`
- **THEN** `businessStore.todos` SHALL 为空且 TodoList SHALL 隐藏

#### Scenario: 未出现 write_todos 的流

- **WHEN** 一次流式回合内 SSE 未出现 `toolName` 为 `write_todos` 的 `tool-input-available`
- **THEN** 前端 SHALL NOT 因本 Requirement 而更新 todos（除非此前回合已写入的快照仍保留，直至清空规则触发）

### Requirement: TodoList 生命周期 SHALL 仅绑定当前流式回合

前端 SHALL 在 **新会话开始、切换会话、或当前流式回合正常结束/中止** 时清空 **`businessStore.todos`**。

前端 **SHALL NOT** 在加载历史消息列表时，根据历史 assistant 消息中的 `write_todos` tool part 自动填充 Todo 面板。

#### Scenario: 刷新或重进历史会话不恢复面板

- **WHEN** 用户刷新页面或重新打开同一会话历史，且消息中曾含 `write_todos` tool part
- **THEN** TodoList SHALL 为空（不展示），直至新的流式回合再次收到 `write_todos` 的 `tool-input-available`

#### Scenario: 切换会话清空 todo

- **WHEN** 用户在 chat 页从会话 A 切换到会话 B
- **THEN** `businessStore.todos` SHALL 被清空

## MODIFIED Requirements

（无。本变更不修改 SSE 事件类型表与桥接层 golden 要求；`write_todos` 的 `tool-input-available` 形状沿用既有 tool 帧契约。）
