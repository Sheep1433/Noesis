## ADDED Requirements

### Requirement: Agent runtime SHALL 支持独立摘要模型的 summarization offload

系统在 `POST /api/chat/sessions/stream` 所驱动的 LangGraph Agent 运行时中，SHALL 支持将长会话摘要步骤从主推理模型中解耦，并通过独立的 summarization model 句柄执行摘要；当未配置独立摘要模型时，系统 SHALL 回退到当前主模型而不改变对外流式协议。

该能力至少 SHALL 在 `COMMON_QA` 路径生效，并通过统一 agent factory 或等价装配层管理，而不是由各 Agent 自行内联配置。摘要触发阈值、保留消息数量与开关 SHALL 可配置。

#### Scenario: 已配置独立摘要模型

- **WHEN** `COMMON_QA` 流式对话达到摘要阈值，且系统已配置 summarization model
- **THEN** 运行时 SHALL 使用独立摘要模型完成上下文压缩，并继续后续主模型推理

#### Scenario: 未配置独立摘要模型

- **WHEN** 流式对话达到摘要阈值，但系统未配置 summarization model
- **THEN** 运行时 SHALL 回退到主模型完成摘要，且 `POST /api/chat/sessions/stream` 的 SSE 事件类型与消息持久化格式 SHALL 保持兼容

### Requirement: Agent runtime SHALL 在工具循环早期检测并收敛

系统在流式问答运行时中，SHALL 在 `recursion_limit` 之外增加显式的循环检测机制，用于识别：

- 相同或等价工具调用集合重复执行；
- 同类工具在短窗口内高频空转。

该机制 SHALL 至少支持 warning 与 hard-stop 两级阈值。达到 warning 阈值时，系统 SHALL 注入要求模型总结当前发现、避免继续重复工具的收敛提示；达到 hard-stop 阈值时，系统 SHALL 阻止继续工具循环，并以可观测的停止语义结束本轮回答。

#### Scenario: 重复工具达到 warning 阈值

- **WHEN** 同一 assistant 回复内，工具调用轨迹在配置窗口内重复达到 warning 阈值，但尚未达到 hard-stop
- **THEN** 系统 SHALL 向后续模型决策注入收敛提示，而不是立即中断整个请求

#### Scenario: 重复工具达到 hard-stop 阈值

- **WHEN** 同一 assistant 回复内，重复工具轨迹继续增长并达到 hard-stop 阈值
- **THEN** 系统 SHALL 阻断继续工具调用，并以明确的停止原因结束流式回答，而不是仅依赖最终 `recursion_limit` 报错

### Requirement: Agent runtime SHALL 修复 dangling tool calls 后再继续模型调用

在任一会话的历史消息中，如果某条 `AIMessage` 已声明 `tool_calls`，但对应 `ToolMessage` 因页面刷新、SSE 断开、取消或异常而缺失，系统 SHALL 在下一轮模型调用前自动修复该历史，使模型看到结构完整的消息序列。

修复方式 SHALL 为在悬空 `tool_call` 后插入 synthetic error `ToolMessage`（或等价结构完整补丁），而不是静默删除原始 `tool_calls`。该修复 SHALL 对 `POST /api/chat/sessions/stream` 的既有 SSE 事件类型保持兼容，且 SHALL 不要求前端新增协议解析。

#### Scenario: 页面刷新导致工具结果缺失

- **WHEN** 某会话上一轮流式回答在 `tool_calls` 已生成后被浏览器刷新中断，数据库或内存历史中缺少对应 `ToolMessage`
- **THEN** 用户在同一会话发起下一轮提问时，系统 SHALL 先补齐 synthetic error `ToolMessage`，再继续模型调用

#### Scenario: 无悬空工具时不修改历史

- **WHEN** 历史消息中的每个 `tool_call_id` 都已有对应 `ToolMessage`
- **THEN** dangling repair SHALL 不额外改写该轮输入消息序列

## MODIFIED Requirements

### Requirement: 流式问答与 SSE 契约

系统 SHALL 通过 `POST /api/chat/sessions/stream`（及设计文档中约定的同前缀端点）以 `text/event-stream` 输出 Noesis SSE 帧；事件流由 `LangGraphSseBridge` 从 LangGraph `astream_events` 转换，包含推理与文本增量、工具调用与输出、错误与结束标记，并以 `data: [DONE]` 收尾。

新增的 summarization offload、loop detection 与 dangling tool repair **SHALL NOT** 引入新的必选 SSE 事件类型；前端既有 `useSSEStream` 与 assistant multipart 渲染路径 SHALL 在不识别新增内部实现细节的前提下继续工作。

#### Scenario: runtime guard 开启时 SSE 仍兼容

- **WHEN** 系统启用 summarization offload、loop detection 与 dangling tool repair，且客户端通过 `POST /api/chat/sessions/stream` 建立流式请求
- **THEN** 输出 SHALL 仍使用既有 SSE 事件类型集合与 `[DONE]` 收尾，前端无需新增事件类型分支即可完成解析
