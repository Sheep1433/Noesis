## ADDED Requirements

### Requirement: chat 页 SHALL 对 task 工具 parts 渲染 SubagentCollapse

在 `chat.vue` 助手消息渲染路径中，当 `content.parts` 中某 part 的 **`type`** 为 **`tool`** 且 **`toolName`**（或同义字段）为 **`task`** 时，前端 SHALL 使用 **`SubagentCollapse`**（或等价专用组件）渲染该 part，**SHALL NOT** 对该 part 使用通用 **`ToolCallCollapse`**。

展示字段 SHALL 至少包含：
- 自 **`input.description`** 派生的任务标题（缺失时使用约定默认文案）；
- 自 **`input.subagent_type`** 派生的子 Agent 类型标签（缺失时使用约定默认值）；
- 自 **`input`**、**`output`**、**`status`**、**`error`** 经 **`parseTaskToolOutput`**（或等价规则）派生的运行状态：`in_progress` | `completed` | `failed`；
- 状态为 **`completed`** 时展示结果摘要（取自 output 中成功前缀之后的内容）；
- 状态为 **`failed`** 时展示错误摘要。

其它 **`toolName`** 且 **无** **`parentTaskCallId`** 的 tool part SHALL 在主界面使用 **`ToolCallCollapse`**。

本 Requirement **不** 要求服务端新增 SSE **事件类型**；流式路径 **SHALL** 继续依赖既有 **`tool-input-*`** / **`tool-output-available`** 写入 assistant **`content.parts`**。

#### Scenario: 流式委派 task 后展示进行中

- **WHEN** 用户于 chat 页发起流式对话，SSE 序列中出现 `tool-input-available`，`toolName` 为 `task`，`input` 含 `{ "description": "检索日志", "subagent_type": "general-purpose" }`，且尚未收到对应 `toolCallId` 的 output
- **THEN** 当前 assistant 消息中 SHALL 出现 SubagentCollapse，标题为「检索日志」，状态为进行中

#### Scenario: task 成功完成后展示结果

- **WHEN** 同一 `toolCallId` 随后收到 `tool-output-available`，output 文本以 `Task Succeeded. Result:` 开头并含结果正文
- **THEN** SubagentCollapse SHALL 状态变为已完成，并展示结果摘要

#### Scenario: task 失败

- **WHEN** 对应 output 以 `Task failed.` 或 `Task timed out` 开头，或 part `status` 为 error
- **THEN** SubagentCollapse SHALL 状态为失败并展示错误摘要

#### Scenario: 历史消息中的 task part 只读恢复

- **WHEN** 用户刷新或重新打开会话，历史 assistant 消息 `content.parts` 中含已完成的 `task` tool part
- **THEN** SubagentCollapse SHALL 根据持久化 part 渲染相同语义的状态与结果，无需额外 Pinia 状态

#### Scenario: 非 task 顶层工具不受影响

- **WHEN** assistant 消息 parts 中含 `toolName` 为 `read_file` 且 **无** `parentTaskCallId` 的 tool part
- **THEN** 前端 SHALL 仍使用 ToolCallCollapse 在主界面渲染，行为与变更前一致

### Requirement: 子 Agent 内部 tool parts SHALL 嵌套在 SubagentCollapse 内展示

当 tool part 携带 **`parentTaskCallId`**（非空，指向某次 **`task`** 调用的 **`toolCallId`**）时，前端 **SHALL NOT** 在主 assistant 气泡的顶层 parts 循环中将其渲染为独立 **`ToolCallCollapse`**；**SHALL** 在对应 **`SubagentCollapse`** 的展开区域内以 **`ToolCallCollapse`**（或等价）展示该 part 的参数与输出。

桥接层 **SHALL** 在子 Agent 执行窗口内的 `on_tool_*` 事件上写入 **`parentTaskCallId`**（SSE `tool-input-available` / `tool-output-available` 的 JSON 可选键，落库 `content.parts` 同键）；**SHALL NOT** 新增 SSE 事件类型。

#### Scenario: 子 Agent 内部 read 不在主界面平铺

- **WHEN** 流式序列中先出现 `toolName=task` 的 `tool-input-available`（`toolCallId=call-task-1`），随后出现 `toolName=read` 的 tool 帧且 **`parentTaskCallId=call-task-1`**
- **THEN** 主界面顶层 **SHALL NOT** 出现独立的 read ToolCallCollapse；用户展开「检索日志」SubagentCollapse **SHALL** 看到 read 工具块

#### Scenario: 主 Agent 顶层 tool 仍平铺

- **WHEN** parts 中含 `toolName=write_todos` 且 **无** `parentTaskCallId`
- **THEN** 该 part **SHALL** 仍在主界面 ToolCallCollapse 展示（并继续驱动 TodoList 等既有逻辑）

#### Scenario: 并行两个 task 时 child 归属正确

- **WHEN** 同一 assistant 消息中存在 `toolCallId=call-task-a` 与 `call-task-b` 两次 task，且各有一次带 `parentTaskCallId=call-task-a` / `call-task-b` 的内部 tool
- **THEN** 各 SubagentCollapse **SHALL** 仅展示归属自身的 child tool parts

#### Scenario: 历史无 parentTaskCallId 时降级

- **WHEN** 持久化 parts 中 task 与内部 read 均无 `parentTaskCallId`（首版旧数据）
- **THEN** 前端 **SHALL** 降级为平铺 ToolCallCollapse，**SHALL NOT** 渲染崩溃

### Requirement: 子 Agent 内部 text/reasoning parts SHALL 嵌套在 SubagentCollapse 内展示

当 `type` 为 **`text`** 或 **`reasoning`** 的 part 携带 **`parentTaskCallId`** 时，前端 **SHALL NOT** 在主 assistant 气泡顶层渲染该 part；**SHALL** 在对应 **`SubagentCollapse`** 展开区「执行过程」时间线内按原序展示（text 为叙述块，reasoning 为思考块）。

桥接层 **SHALL** 在子 Agent 执行窗口内的 `on_chat_model_stream` 事件上，为 **`text-start`** / **`text-delta`** / **`reasoning-start`** / **`reasoning-delta`** 写入 **`parentTaskCallId`**（与 tool 打标规则一致）；**SHALL NOT** 新增 SSE 事件类型。

#### Scenario: 子 Agent 叙述文本不在主界面泄漏

- **WHEN** 流式序列中 `task` 执行期间出现 `text-delta` 且 **`parentTaskCallId`** 指向该 `task` 的 `toolCallId`
- **THEN** 主界面顶层 **SHALL NOT** 出现该段文本；展开 SubagentCollapse **SHALL** 在「执行过程」内可见

### Requirement: 流式帧与 parts SHALL 支持可选字段 parentTaskCallId

系统 SHALL 在既有 **`tool-input-available`** / **`tool-output-available`**、**`text-start`** / **`text-delta`**、**`reasoning-start`** / **`reasoning-delta`** 事件的 `data` JSON 上支持可选字符串字段 **`parentTaskCallId`**，表示该片段归属于哪一次 **`task`** 的 **`toolCallId`**；未携带时语义 SHALL 与改造前一致（顶层 part）。

落库 assistant 消息 **`content.parts`** 中 **MAY** 含同名字段（或 snake_case **`parent_task_call_id`**，前端 normalize 时 **SHALL** 统一为 camelCase）。

#### Scenario: 桥接层 contract 测试

- **WHEN** pytest 向 `LangGraphSseBridge` 注入带 `parent_ids` 的 mock `on_tool_start`（非 task，且处于 task 栈内）
- **THEN** 输出的 `tool-input-available` SSE `data` JSON **SHALL** 含 `parentTaskCallId` 且值为当前 task 的 `toolCallId`

### Requirement: task 工具解析 SHALL 防御性处理非法 input/output

前端 SHALL 提供可单测的解析入口（如 **`parseTaskToolInput`**、**`parseTaskToolOutput`**），对缺失或非字符串字段使用约定默认值或跳过展示，**SHALL NOT** 因非法 JSON 导致 chat 页渲染崩溃。

#### Scenario: input 缺少 description

- **WHEN** `task` part 的 `input` 无 `description` 字段
- **THEN** SubagentCollapse SHALL 使用约定默认标题仍正常渲染

#### Scenario: output 为空且 status 为 running

- **WHEN** `task` part 无 output 且 `status` 为 running 或 streaming
- **THEN** 解析结果 SHALL 为 `in_progress`

## MODIFIED Requirements

（无。本变更不修改既有 Requirement 标题；`parentTaskCallId` 与 Subagent 展示为新增 Requirement。）
