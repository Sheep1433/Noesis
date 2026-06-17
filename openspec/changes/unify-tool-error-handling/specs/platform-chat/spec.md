## ADDED Requirements

### Requirement: tool-output-available 错误帧 SHALL 携带可选 errorCategory

当 `domain/chat/streaming/langgraph_sse.py` 中的 `LangGraphSseBridge` 因 `on_tool_end`（`ToolMessage.status=error`）或 `on_tool_error` 发出 `tool-output-available` 且 `status` 为 `error` 时，SSE `data:` JSON SHALL 除既有 `error` 字符串外，支持可选字符串字段 **`errorCategory`**，取值为 `agent-tool-failure-handling` 规格中定义的分类枚举名。用户可见 `error` SHALL 为固定短句之一（连接失败 / 执行超时 / 参数错误 / 环境不可用 / 已停止 / 执行失败），SHALL NOT 暴露堆栈或英文 middleware 模板。

客户端 SHALL 允许忽略未知 `errorCategory`；未携带时行为 SHALL 与改造前一致（仅展示 `error`）。

#### Scenario: 分类后的错误 SSE 帧

- **WHEN** 某工具调用以 `execution_timeout` 分类失败且 bridge 处理 `on_tool_end`
- **THEN** 发出的 `tool-output-available` SHALL 含 `status=error`、`error`（用户可见摘要）、`errorCategory=execution_timeout`，且 SHALL 含非负 `durationMs`（若可计算）

#### Scenario: 旧客户端忽略未知字段

- **WHEN** 客户端 SSE 解析逻辑未识别 `errorCategory`
- **THEN** 客户端 SHALL 仍可仅依据 `error` 与 `status` 完成渲染，SHALL NOT 因新增字段而解析失败

### Requirement: assistant 落库 tool part SHALL 与 SSE 错误语义一致

当流式路径产生 `status=error` 的 `tool-output-available` 时，对应 assistant 消息 `content.parts` 中的 tool part SHALL 满足：

- `status` 为 `error`；
- `error` 与 SSE 帧中用户可见摘要一致；
- 若 SSE 含 `errorCategory`，落库 part SHALL 含同名可选键 `errorCategory`。

`domain/chat/message_builder.py` 中 `AssistantMessageBuilder.append_tool_output` SHALL 按 `tool_call_id` 定位目标 part；若定位失败，`langgraph_sse.py` SHALL 经 `common.logging` 记录 warning，SHALL NOT 静默丢弃错误状态。

#### Scenario: 并行工具错误落库不错位

- **WHEN** 同一 assistant 回合内两个并行工具调用先后结束，且其中一个为 `status=error`
- **THEN** 错误输出 SHALL 写入 `tool_call_id` 对应的 tool part，SHALL NOT 覆盖另一并行工具 part 的 `output`/`status`

#### Scenario: builder 定位失败可观测

- **WHEN** bridge 收到某 `tool_call_id` 的工具结束事件，但 builder 中不存在对应 running part
- **THEN** 系统 SHALL 记录包含 `tool_call_id` 与工具名的 warning 日志，SHALL NOT 使用 bare `except: pass` 静默忽略

## MODIFIED Requirements

### Requirement: tool-output-available SHALL 携带单次工具调用耗时

系统在 `domain/chat/streaming/langgraph_sse.py` 的 `LangGraphSseBridge` 处理 `on_tool_end` 或 `on_tool_error` 并发出 **`tool-output-available`** 时，SHALL 在 `data:` JSON 中增加可选数值字段 **`durationMs`**，表示自对应 **`on_tool_start`**（或同 **`toolCallId`** 的开始时刻）至工具结束的服务端耗时，单位为毫秒，为非负整数。

当 `status` 为 `error` 时，同一帧 SHALL 同时携带用户可见 **`error`** 摘要；若已实现统一分类，SHALL 可选携带 **`errorCategory`**（见本 change ADDED Requirement）。错误帧的 `output` 字段 SHALL 为空字符串或省略，SHALL NOT 用 `output` 承载未脱敏堆栈。

#### Scenario: 成功工具耗时

- **WHEN** bridge 收到 `on_tool_end` 且工具输出 `status` 不为 `error`
- **THEN** 发出的 `tool-output-available` SHALL 含 `durationMs`，且该 tool part 落库后 SHALL 含相同语义的非负整数

#### Scenario: 失败工具仍上报耗时

- **WHEN** bridge 收到 `on_tool_error` 或 `on_tool_end` 且工具输出 `status=error`
- **THEN** 发出的 `tool-output-available`（`status=error`）SHALL 仍含 `durationMs`（若可计算），且 SHALL 含脱敏后的 `error` 字段
