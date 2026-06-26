## ADDED Requirements

### Requirement: tool-output-available 成功帧 SHALL 携带可选 outcome 元数据

当 `LangGraphSseBridge` 发出 `status=success` 的 **`tool-output-available`** 时，SSE `data:` JSON SHALL 支持下列可选字段（camelCase），语义见 `agent-tool-failure-handling` 规格：

| 字段 | 类型 | 说明 |
|------|------|------|
| `outcome` | string | `ok` \| `empty` \| `command_failed` \| `timed_out` |
| `exitCode` | number | 进程退出码（可解析时） |
| `timedOut` | boolean | 是否超时终止 |
| `truncated` | boolean | 输出是否截断 |

客户端 SHALL 允许忽略未知字段；缺省 `outcome` 时，有非空 `output` 则按 `ok` 渲染，无 `output` 则按改造前行为（本 change 实施后前端 SHALL 将无 output 的 success 帧视为 `empty` 并展示占位）。

#### Scenario: success 帧携带 command_failed

- **WHEN** bridge 处理 `execute` 结束且解析得 `outcome=command_failed`、`exitCode=2`
- **THEN** `tool-output-available` SHALL 含 `status=success`、`outcome=command_failed`、`exitCode=2`

#### Scenario: 旧客户端忽略 outcome

- **WHEN** 客户端未实现 `outcome` 解析
- **THEN** SHALL 仍可依据 `status` 与 `output` 渲染，SHALL NOT 解析失败

### Requirement: assistant 落库 tool part SHALL 持久化 outcome 元数据

当 SSE `tool-output-available` 含 `outcome` / `exitCode` / `timedOut` / `truncated` 时，对应 `content.parts` tool part SHALL 持久化等价字段（`outcome`、`exitCode` 等同名或项目既有 snake_case 约定，前后端 reduce 层 SHALL 统一映射）。

`append_tool_output` 定位失败时 SHALL 记录 warning（既有 Requirement 不变）。

#### Scenario: 历史消息加载 outcome

- **WHEN** 客户端加载含 `outcome: "empty"` 的 tool part
- **THEN** `ToolCallCollapse` SHALL 展示「（无输出）」

## MODIFIED Requirements

### Requirement: tool-output-available 错误帧 SHALL 携带可选 errorCategory

当 `domain/chat/streaming/langgraph_sse.py` 中的 `LangGraphSseBridge` 因 `on_tool_end`（`ToolMessage.status=error`）或 `on_tool_error` 发出 `tool-output-available` 且 `status` 为 `error` 时，SSE `data:` JSON SHALL 除既有 `error` 字符串外，支持可选字符串字段 **`errorCategory`**，取值为 `agent-tool-failure-handling` 规格中定义的**调用失败**分类枚举名。用户可见 `error` SHALL 为固定短句之一（连接失败 / 执行超时 / 参数错误 / 环境不可用 / 已停止 / 执行失败），SHALL NOT 暴露堆栈或英文 middleware 模板。

**`errorCategory` 仅用于 `status=error`（调用失败）**；`status=success` 时的 `command_failed` / `timed_out` SHALL 使用 `outcome` 字段表达，SHALL NOT 复用 `errorCategory`。

客户端 SHALL 允许忽略未知 `errorCategory`；未携带时行为 SHALL 与改造前一致（仅展示 `error`）。

#### Scenario: 分类后的错误 SSE 帧

- **WHEN** 某工具调用以 `execution_timeout` 分类失败且 bridge 处理 `on_tool_end`
- **THEN** 发出的 `tool-output-available` SHALL 含 `status=error`、`error`（用户可见摘要）、`errorCategory=execution_timeout`，且 SHALL 含非负 `durationMs`（若可计算）

#### Scenario: 命令失败不使用 errorCategory

- **WHEN** `execute` 返回 `exit_code=1` 且 `status=success`
- **THEN** SSE SHALL 含 `outcome=command_failed`
- **AND** SHALL NOT 含 `errorCategory`

#### Scenario: 旧客户端忽略未知字段

- **WHEN** 客户端 SSE 解析逻辑未识别 `errorCategory` 或 `outcome`
- **THEN** 客户端 SHALL 仍可完成渲染，SHALL NOT 因新增字段而解析失败

### Requirement: assistant 落库 tool part SHALL 与 SSE 错误语义一致

当流式路径产生 `status=error` 的 `tool-output-available` 时，对应 assistant 消息 `content.parts` 中的 tool part SHALL 满足：

- `status` 为 `error`；
- `error` 与 SSE 帧中用户可见摘要一致；
- 若 SSE 含 `errorCategory`，落库 part SHALL 含同名可选键 `errorCategory`。

当流式路径产生 `status=success` 且含 `outcome` 的 `tool-output-available` 时，tool part SHALL 同步持久化 `outcome` 及可选 `exitCode` / `timedOut` / `truncated`；**`error` 字段 SHALL 为空或缺省**（除非同时存在调用失败，本场景不应发生）。

`domain/chat/message_builder.py` 中 `AssistantMessageBuilder.append_tool_output` SHALL 按 `tool_call_id` 定位目标 part；若定位失败，`langgraph_sse.py` SHALL 经 `common.logging` 记录 warning，SHALL NOT 静默丢弃错误状态。

#### Scenario: 并行工具错误落库不错位

- **WHEN** 同一 assistant 回合内两个并行工具调用先后结束，且其中一个为 `status=error`
- **THEN** 错误输出 SHALL 写入 `tool_call_id` 对应的 tool part，SHALL NOT 覆盖另一并行工具 part 的 `output`/`status`

#### Scenario: builder 定位失败可观测

- **WHEN** bridge 收到某 `tool_call_id` 的工具结束事件，但 builder 中不存在对应 running part
- **THEN** 系统 SHALL 记录包含 `tool_call_id` 与工具名的 warning 日志，SHALL NOT 使用 bare `except: pass` 静默忽略

## ADDED Requirements

### Requirement: ToolCallCollapse SHALL 展示执行层 outcome

前端 `ToolCallCollapse`（及 `SubagentCollapse` 内嵌工具块）SHALL 依据 `status` 与 `outcome` 渲染：

| `status` | `outcome` | header 标签 |
|----------|-----------|------------|
| `error` | — | 错误 |
| `success` | `ok` 或缺省 | 完成 |
| `success` | `empty` | 完成 |
| `success` | `command_failed` | 命令失败 |
| `success` | `timed_out` | 执行超时 |

输出区规则：

- `outcome=empty` 或 `status=success` 且无 output 文本：SHALL 展示「（无输出）」占位，SHALL NOT 隐藏整个输出 section；
- `outcome=command_failed`：SHALL 展示 output（若有）并标注 `退出码: {exitCode}`（当 `exitCode` 存在）；
- `outcome=timed_out`：SHALL 展示 output（若有）并标注超时提示。

#### Scenario: 空输出 execute 展开可见

- **WHEN** 用户展开 `execute` 工具块，part 含 `status=success`、`outcome=empty`、无 output
- **THEN** UI SHALL 在输出区显示「（无输出）」

#### Scenario: 命令失败展示退出码

- **WHEN** part 含 `outcome=command_failed`、`exitCode=127`
- **THEN** header SHALL 显示「命令失败」标签，输出区 SHALL 含 `退出码: 127`
