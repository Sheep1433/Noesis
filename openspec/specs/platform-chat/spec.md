## Purpose

本能力规定 Noesis **聊天平台基础设施**：MySQL 持久化的会话与消息（含 multipart `content.parts`）、非流式与流式（SSE）问答入口、`LangGraphSseBridge` 通用帧契约、保活与稳定性、停止生成、LLM 工厂路由，以及 chat 页通用 UI（工具折叠、思考块、子 Agent、Todo、token 展示）。

四个 Agent 场景的专属流水线、业务 SSE 帧与页面 **SHALL** 分别以以下 spec 为单一事实来源，本 spec **不**重复其场景细节：

| `qa_type` | 能力 spec |
|-----------|-----------|
| `COMMON_QA` | `openspec/specs/agent-common-qa/spec.md` |
| `FAULT_OPERATION_QA` | `openspec/specs/agent-fault-operation/spec.md` |
| `TEST_CASE_QA` | `openspec/specs/agent-test-case/spec.md` |
| `DEEP_RESEARCH_QA` | `openspec/specs/agent-deep-research/spec.md` |
## Requirements
### Requirement: 会话生命周期管理

系统 SHALL 通过 `/api/chat/sessions` 系列接口支持会话列表、创建、详情、删除、标题更新、子会话列表与批量删除；删除为软删除或业务约定下的不可见，且与会话归属用户绑定。

系统 SHALL 提供 **`PUT /api/chat/sessions/{session_id}/ensure`**，按 client 提供的 `session_id` 幂等调用 `get_or_create_session`，供发送前物化会话（见 `chat-composer-send-upload`）。

#### Scenario: 创建或复用会话

- **WHEN** 流式或非流式请求携带 `chat_id`（或等价 session 标识）
- **THEN** 系统 SHALL 在不存在时创建会话，存在时关联同一会话，并在 `extra` 等字段中维护 `qa_type` 等元数据

#### Scenario: ensure 物化 client session_id

- **WHEN** 客户端对尚未入库的 `{session_id}` 调用 `PUT .../ensure` 且 JWT 有效
- **THEN** 系统 SHALL 返回 200 并创建或返回已有会话记录

### Requirement: 消息列表与详情

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/messages` 与 `GET /api/chat/messages/{message_id}`，返回角色、multipart 内容、状态与时间戳，且用户仅能访问本人会话下的消息。

#### Scenario: 越权访问

- **WHEN** 用户请求不属于自己会话的消息或会话
- **THEN** 系统 SHALL 返回 404 或业务约定的无权限结果，且不得泄漏其他用户内容

### Requirement: 流式问答与 SSE 核心契约

系统 SHALL 通过 `POST /api/chat/sessions/stream`（及设计文档中约定的同前缀端点）以 `text/event-stream` 输出 Noesis SSE 帧；事件流由 `LangGraphSseBridge` 从 LangGraph `astream_events` 转换，包含推理与文本增量、工具调用与输出、错误与结束标记，并以 `data: [DONE]` 收尾。

新增的 summarization offload、loop detection 与 dangling tool repair **SHALL NOT** 引入新的必选 SSE 事件类型；前端既有 `useSSEStream` 与 assistant multipart 渲染路径 SHALL 在不识别新增内部实现细节的前提下继续工作。

#### Scenario: runtime guard 开启时 SSE 仍兼容

- **WHEN** 系统启用 summarization offload、loop detection 与 dangling tool repair，且客户端通过 `POST /api/chat/sessions/stream` 建立流式请求
- **THEN** 输出 SHALL 仍使用既有 SSE 事件类型集合与 `[DONE]` 收尾，前端无需新增事件类型分支即可完成解析

### Requirement: qa_type 路由

系统 SHALL 在 `qa_service`（及等价问答入口）根据请求体 `qa_type` 路由至已注册 Agent 流水线，并在会话 `extra` 中记录该类型。

| `qa_type` | Agent / 协调器 | 详细 spec |
|-----------|----------------|-----------|
| `COMMON_QA` | `GeneralQAAgent` | `agent-common-qa` |
| `FAULT_OPERATION_QA` | `FaultOperationAgent` | `agent-fault-operation` |
| `TEST_CASE_QA` | `CaseCoordinator` | `agent-test-case` |
| `DEEP_RESEARCH_QA` | `DeepResearchAgent` | `agent-deep-research` |

未知或未注册的 `qa_type` **SHALL NOT** 静默进入任一 Agent；错误处理遵循项目 API 约定。

#### Scenario: 流式请求携带已注册 qa_type

- **WHEN** 客户端对 `POST /api/chat/sessions/stream` 提交合法载荷且 `qa_type` 为上表四者之一
- **THEN** 系统 SHALL 调用对应 Agent 流水线，且会话元数据中记录该 `qa_type`

#### Scenario: 未知 qa_type

- **WHEN** 请求体 `qa_type` 不在已注册枚举内
- **THEN** 系统 SHALL 返回明确业务错误，且 SHALL NOT 调用任一 Agent 流水线

### Requirement: SSE 传输稳定性与超时对齐

系统在反向代理或长时间工具/模型等待场景下 SHALL 避免「无字节空闲」导致的静默断连与半包解析错误；具体数值与保活策略以 `docs/prd/platform/SSE流式数据设计.md` §6 为单一事实来源，并与部署层 `proxy_read_timeout`（或等价）、Uvicorn/上游 LLM 超时一致。

#### Scenario: 代理空闲阈值大于工具耗时

- **WHEN** 单次流式回答在两次业务 SSE 帧之间可能间隔超过默认反向代理读超时（例如 60s）
- **THEN** 运维或应用实现 SHALL 满足 §6.1：调大代理超时和/或按 §6.1 发送 SSE 注释保活帧，且保活帧不得被前端当作业务 JSON 解析

#### Scenario: TCP 分片导致的半包

- **WHEN** 浏览器以多个 chunk 交付同一 SSE 帧且帧尾未以 `\n\n` 结束
- **THEN** 前端 SHALL 缓冲并在流关闭时按 §6.2 规则 flush，确保 `finish` / `[DONE]` 等尾帧可被解析

#### Scenario: 连接已断开仍向 socket 写入

- **WHEN** 客户端已关闭连接而服务端仍尝试写入 SSE 字节
- **THEN** 系统 SHALL 将典型连接类错误降级为可预期的取消/断开日志（见 §6.4），不得将此类情况与未捕获的业务异常混同处理策略

### Requirement: 服务端 SHALL 按可配置间隔发送 SSE 注释保活帧

在 `POST /api/chat/sessions/stream` 及共用同一桥接层的其它流式聊天端点的流式响应中，当距离上一次已写入客户端的字节（含任意业务 SSE 帧或注释保活帧）超过配置阈值且流仍未正常结束时，系统 SHALL 写入一条符合 SSE 规范的注释行帧（以 `:` 开头、以空行结束），且该帧 SHALL 不包含 `event:` 或业务 `data:` JSON，以免干扰现有前端解析。

#### Scenario: 保活间隔为正且上游长时间无输出

- **WHEN** `sse_keepalive_interval_seconds`（环境变量 `SSE_KEEPALIVE_INTERVAL_SECONDS`）为大于 0 的数值，且 Agent 异步生成器在超过该秒数内未产出下一项
- **THEN** 系统 SHALL 向响应流写入至少一条 SSE 注释保活帧，随后继续等待上游事件直至结束或取消

#### Scenario: 保活被显式关闭

- **WHEN** 配置项将保活间隔设为 0（或项目约定的「关闭」值）
- **THEN** 系统 SHALL 不发送注释保活帧，流行为与关闭该功能时一致

### Requirement: 连接类写入失败 SHALL 可观测且不降级为未分类业务错误

当客户端已断开或连接重置导致无法继续向响应体写入时，系统 SHALL 停止继续向该连接写入，并将该情况记录为 INFO 或 WARNING 级别日志（不得默认使用 `logger.exception` 视为未处理应用错误），且 SHALL 不因此损坏数据库会话的显式 `rollback`/`commit` 约定以外的逻辑。

#### Scenario: 写入阶段触发 BrokenPipe

- **WHEN** `StreamingResponse` 消费循环在 `yield` 编码后的 SSE 字节时抛出 `BrokenPipeError` 或 `ConnectionResetError`
- **THEN** 系统 SHALL 结束该次流的写入循环，并采用 INFO 或 WARNING 记录断开事实

### Requirement: SSE 对外契约 SHALL 具备自动化回归覆盖

系统 SHALL 为 `LangGraphSseBridge`（或统一的 SSE 字符串格式化入口）提供自动化测试，覆盖至少：`message-start` 形态、`text-delta` 含 `textDelta`、`finish` 含 `finishReason`/`usage`、`data: [DONE]` 收尾；断言方式为解析 `data:` 行 JSON 键集合或关键字段类型，防止静默破坏 `useSSEStream` 消费者。

golden 或等价测试 **SHALL** 覆盖 **`tool-output-available.duration_ms`** 类型、**`usage-update`** 帧的 `type`/`messageId`/`usage` 键，以及多轮 LLM 累计后 **`finish.usage`** 与最后一次 **`usage-update`** 一致。

场景专属业务帧（如 `TEST_CASE_QA` 的 `phase-*`）的 golden 断言 **SHALL** 在对应 `agent-*` spec 中规定，本 Requirement **不**要求平台 golden 覆盖 `phase-*`。

#### Scenario: 桥接层最小 golden 断言

- **WHEN** 测试向桥接传入代表「消息开始」与「文本增量」的合成事件并收集输出字符串
- **THEN** 输出 SHALL 包含合法 SSE 帧边界且 `data:` 负载可被 `json.loads` 解析，且 `type` 字段与事件名一致

#### Scenario: 工具耗时与 usage-update golden 断言

- **WHEN** 测试向桥接传入配对的 `on_tool_start`/`on_tool_end` 及含 `usage_metadata` 的 `on_chat_model_end` 合成事件并收集输出
- **THEN** 输出 SHALL 含 `tool-output-available` 且 `duration_ms` 为非负整数，SHALL 含 `usage-update` 且 `usage.input_tokens`/`usage.output_tokens` 为数值，且最终 `finish.usage` SHALL 与累计一致

### Requirement: tool-output-available SHALL 携带单次工具调用耗时

系统在 `LangGraphSseBridge` 处理 `on_tool_end` 或 `on_tool_error` 并发出 **`tool-output-available`** 时，SHALL 在 `data:` JSON 中增加可选数值字段 **`duration_ms`**（snake_case），表示自对应 **`on_tool_start`**（或同 **`tool_call_id`** 的开始时刻）至工具结束的服务端耗时，单位为毫秒，为非负整数。

同一 **`tool_call_id`** 的工具 part 在 reduce 至 assistant **`content.parts`** 时 SHALL 持久化等价的 **`duration_ms`** 字段，供历史消息渲染。

前端对缺失 **`duration_ms`** 的帧或 part SHALL 兼容忽略，不得导致解析或渲染失败。

#### Scenario: 工具成功结束携带耗时

- **WHEN** Agent 执行一次工具调用，bridge 收到配对的 `on_tool_start` 与 `on_tool_end`
- **THEN** 发出的 `tool-output-available` SHALL 含 `duration_ms`，且该 tool part 落库后 SHALL 含相同语义的非负整数

#### Scenario: 工具错误结束仍记录耗时

- **WHEN** bridge 收到 `on_tool_error` 或 `on_tool_end` 且工具输出 `status=error`
- **THEN** 发出的 `tool-output-available`（`status=error`）SHALL 仍含 `duration_ms`（若可计算），且 SHALL 含脱敏后的 `error` 字段与可选 `errorCategory`

#### Scenario: 历史消息恢复工具耗时

- **WHEN** 客户端加载 assistant 消息，其 `content.parts` 中某 tool part 含 `duration_ms: 1200`
- **THEN** chat 页 SHALL 在对应工具折叠块展示约 1.2s 量级耗时，无需依赖 SSE 重放

### Requirement: tool-output-available 错误帧 SHALL 携带可选 errorCategory

当 `domain/chat/streaming/langgraph_sse.py` 中的 `LangGraphSseBridge` 因 `on_tool_end`（`ToolMessage.status=error`）或 `on_tool_error` 发出 `tool-output-available` 且 `status` 为 `error` 时，SSE `data:` JSON SHALL 除既有 `error` 字符串外，支持可选字符串字段 **`errorCategory`**，取值为 `agent-tool-failure-handling` 规格中定义的**调用失败**分类枚举名。用户可见 `error` SHALL 为固定短句之一（连接失败 / 执行超时 / 参数错误 / 环境不可用 / 已停止 / 执行失败），SHALL NOT 暴露堆栈或英文 middleware 模板。

**`errorCategory` 仅用于 `status=error`**；`status=success` 时的 `command_failed` / `timed_out` SHALL 使用 `outcome` 字段（见下条 Requirement），SHALL NOT 复用 `errorCategory`。

客户端 SHALL 允许忽略未知 `errorCategory`；未携带时行为 SHALL 与改造前一致（仅展示 `error`）。

#### Scenario: 分类后的错误 SSE 帧

- **WHEN** 某工具调用以 `execution_timeout` 分类失败且 bridge 处理 `on_tool_end`
- **THEN** 发出的 `tool-output-available` SHALL 含 `status=error`、`error`（用户可见摘要）、`errorCategory=execution_timeout`，且 SHALL 含非负 `duration_ms`（若可计算）

#### Scenario: 命令失败不使用 errorCategory

- **WHEN** `execute` 返回 `exit_code=1` 且 `status=success`
- **THEN** SSE SHALL 含 `outcome=command_failed`，SHALL NOT 含 `errorCategory`

#### Scenario: 旧客户端忽略未知字段

- **WHEN** 客户端 SSE 解析逻辑未识别 `errorCategory` 或 `outcome`
- **THEN** 客户端 SHALL 仍可仅依据 `error` 与 `status` 完成渲染，SHALL NOT 因新增字段而解析失败

### Requirement: tool-output-available 成功帧 SHALL 携带 outcome 元数据

当 `LangGraphSseBridge` 发出 `status=success` 的 **`tool-output-available`** 时，SSE `data:` JSON SHALL 支持下列字段（snake_case，语义见 `agent-tool-failure-handling`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `outcome` | string | `ok` \| `empty` \| `command_failed` \| `timed_out` |
| `exit_code` | number | 进程类可解析时 |
| `timed_out` | boolean | 进程类可解析时 |
| `truncated` | boolean | 可选 |

新流式消息 **SHALL** 含 `outcome`。`output` 字段 SHALL 为用户格式化正文（非 Agent 原始 JSON），规则见 `agent-tool-failure-handling`「用户 output 格式化」Requirement。

落库 tool part SHALL 与 SSE 同名字段对齐。

#### Scenario: success 帧携带 command_failed

- **WHEN** bridge 解析得 `outcome=command_failed`、`exit_code=2`
- **THEN** `tool-output-available` SHALL 含 `status=success`、`outcome=command_failed`、`exit_code=2`

#### Scenario: 旧客户端忽略 outcome

- **WHEN** 客户端未实现 `outcome` 解析
- **THEN** SHALL 仍可依据 `status` 与 `output` 渲染，SHALL NOT 解析失败

### Requirement: assistant 落库 tool part SHALL 与 SSE 错误语义一致

当流式路径产生 `status=error` 的 `tool-output-available` 时，对应 assistant 消息 `content.parts` 中的 tool part SHALL 满足：

- `status` 为 `error`；
- `error` 与 SSE 帧中用户可见摘要一致；
- 若 SSE 含 `errorCategory`，落库 part SHALL 含同名可选键 `errorCategory`。

当流式路径产生 `status=success` 且含 `outcome` 的 `tool-output-available` 时，tool part SHALL 同步持久化 `outcome` 及可选 `exit_code` / `timed_out` / `truncated`；**`error` 字段 SHALL 为空或缺省**。

`domain/chat/message_builder.py` 中 `AssistantMessageBuilder.append_tool_output` SHALL 按 `tool_call_id` 定位目标 part；若定位失败，`langgraph_sse.py` SHALL 经 `common.logging` 记录 warning，SHALL NOT 静默丢弃错误状态。

#### Scenario: 并行工具错误落库不错位

- **WHEN** 同一 assistant 回合内两个并行工具调用先后结束，且其中一个为 `status=error`
- **THEN** 错误输出 SHALL 写入 `tool_call_id` 对应的 tool part，SHALL NOT 覆盖另一并行工具 part 的 `output`/`status`

#### Scenario: builder 定位失败可观测

- **WHEN** bridge 收到某 `tool_call_id` 的工具结束事件，但 builder 中不存在对应 running part
- **THEN** 系统 SHALL 记录包含 `tool_call_id` 与工具名的 warning 日志，SHALL NOT 使用 bare `except: pass` 静默忽略

### Requirement: 流式路径 SHALL 发出消息级累计 LLM token 的 usage-update 与 finish.usage

系统在 **`LangGraphSseBridge`** 中 SHALL 从 LangChain **`on_chat_model_end`** 和/或 stream 末 chunk 的 **`usage_metadata`** 提取 token 计数，按 **`run_id`** 去重后累计至当前 assistant 消息的 **`usage_cumulative`**（含 **`input_tokens`**、**`output_tokens`**，及可选 **`total_tokens`**）。

每完成一轮 LLM 调用（累计值更新后），系统 SHALL 发出 SSE 事件 **`usage-update`**，其 `data:` JSON SHALL 包含至少：

- **`type`**: `"usage-update"`
- **`messageId`**: 当前 assistant 消息 ID
- **`usage`**: 对象，键为 **`input_tokens`**、**`output_tokens`**，及可选 **`total_tokens`**，数值为**自本轮 `message-start` 起所有已完成 LLM 调用的累计**，而非单次 LLM 调用量

系统在发出 **`finish`** 时，其 **`usage`** 字段 SHALL 与最后一次 **`usage-update`** 的累计值一致（若无任何 provider usage 则 MAY 为空对象）。assistant 消息 **`extra.usage`** 持久化 SHALL 与 **`finish.usage`** 一致。

本 Requirement **SHALL NOT** 要求 LangChain AgentMiddleware 作为 token 数据来源；累计逻辑 SHALL 集中在桥接层，适用于经 **`astream_events`** 输出的各 **`qa_type`** 路径。

#### Scenario: 单轮 LLM 完成后发出 usage-update 与 finish

- **WHEN** 一次流式回合仅含一轮 LLM 且 provider 返回 `usage_metadata` `{ input_tokens: 100, output_tokens: 50, total_tokens: 150 }`
- **THEN** SSE 序列 SHALL 在 LLM 轮次结束后含 `usage-update`，其 `usage` 为 `{ input_tokens: 100, output_tokens: 50, total_tokens: 150 }`，且随后 `finish.usage` SHALL 相同

#### Scenario: 多轮 LLM 累计 token

- **WHEN** 同一 assistant 消息内先后完成两轮 LLM，第一轮 usage 为 input 100 / output 50，第二轮为 input 200 / output 80
- **THEN** 第二轮结束后的 `usage-update` 与 `finish.usage` SHALL 为 `{ input_tokens: 300, output_tokens: 130, total_tokens: 430 }`（或 provider 未提供 total 时省略 total）

#### Scenario: 同一 run_id 不重复累计

- **WHEN** 同一 LLM 调用的 `usage_metadata` 在 stream 末 chunk 与 `on_chat_model_end` 各出现一次且 `run_id` 相同
- **THEN** 累计值 SHALL 仅计入一次该轮 usage

#### Scenario: provider 无 usage 时不阻塞 finish

- **WHEN** 流式回合结束但全程未获得 `usage_metadata`
- **THEN** 系统 SHALL 仍发出 `finish` 与 `[DONE]`，且 MAY 省略 `usage-update` 或发出空 usage；前端 SHALL 不展示 token 行

### Requirement: chat 页 SHALL 展示工具耗时与消息级累计 token

系统 SHALL 在 **`chat.vue`**（及共用 **`MessagePartsRenderer`** 的 assistant 渲染路径）中：

- 当 tool part 或流式 **`tool-output-available`** 含 **`duration_ms`** 时，前端 SHALL 在 **`ToolCallCollapse`**（及 **`SubagentCollapse`** 等等价 tool 展示）header 区域展示格式化的单次工具耗时（如秒级 `1.2s`）。
- 当收到 **`usage-update`** 或 **`finish`** 且 **`usage`** 含有效 token 计数时，前端 SHALL 在当前 assistant 消息底部展示**累计** token 摘要（至少区分 input 与 output；有 total 时可一并展示）。
- 展示 SHALL 使用**累计语义**（整条 assistant 回复），SHALL NOT 将单次 LLM 轮次用量作为最终唯一展示（除非该回合仅一轮且与累计相同）。
- 流式过程中 SHALL 随 **`usage-update`** 更新显示；**`finish`** 后定格。加载历史时 SHALL 从 **`message.extra.usage`** 与 **`parts[].duration_ms`** 恢复，无需 SSE 重放。

前端对未知 SSE 键或缺失字段 SHALL 兼容忽略。

#### Scenario: 流式多工具多轮 LLM 的可视化

- **WHEN** 用户于 chat 页发起流式对话，SSE 含两次 `tool-output-available`（各带 `duration_ms`）及两次 `usage-update`（第二次累计大于第一次），最后 `finish`
- **THEN** 界面 SHALL 展示两个工具各自的耗时，且 token 行 SHALL 显示第二次 `usage-update` 的累计值并在 finish 后保持不变

#### Scenario: 无 token 数据时不占位

- **WHEN** 一次流式回合的 `finish.usage` 为空或无有效 token 字段
- **THEN** assistant 消息 SHALL 不展示 token 摘要行（或等价隐藏），工具耗时仍按 part 正常展示

### Requirement: ToolCallCollapse SHALL 展示执行层 outcome

`ToolCallCollapse`（及 `SubagentCollapse` 内嵌工具块）SHALL 依据 `status` 与 `outcome` 渲染（缺省归一化见 `agent-tool-failure-handling`）：

| `status` | `outcome` | header 标签 | 输出区 |
|----------|-----------|------------|--------|
| `error` | — | 错误 | `error` 短句 |
| `success` | `ok` 或缺省且有 `output` | 完成 | `output` 正文 |
| `success` | `empty` 或缺省且无 `output` | 完成 | 「（无输出）」 |
| `success` | `command_failed` | 命令失败 | `output` + 可选 `退出码: {exit_code}` |
| `success` | `timed_out` | 执行超时 | `output` + 超时提示 |

`useSSEStream` / `messageParts.applyToolOutput` SHALL 透传 `outcome`、`exit_code`、`timed_out`、`truncated`。

#### Scenario: 空输出 execute 展开可见

- **WHEN** 用户展开 `execute` 工具块，part 含 `status=success`、`outcome=empty`
- **THEN** UI SHALL 在输出区显示「（无输出）」，SHALL NOT 因 `output` 为空而隐藏整个输出 section

#### Scenario: 命令失败展示退出码

- **WHEN** part 含 `outcome=command_failed`、`exit_code=127`
- **THEN** header SHALL 显示「命令失败」，输出区 SHALL 含 `退出码: 127`（当 `exit_code` 存在）

### Requirement: 停止生成

系统 SHALL 提供 `POST /api/chat/sessions/{session_id}/stop`，作为**用户主动停止**的唯一业务入口：取消进行中的流式任务、将已生成的 assistant 内容以 multipart 格式权威落库，并通过既有 SSE 连接发出 `abort` 与 `finish`（`finish_reason=stopped`）及 `data: [DONE]` 收尾。

客户端在用户点击「停止生成」或加载中二次触发发送（停止）时 **SHALL ONLY** 调用上述 stop 接口（及页面关闭时等价的 `sendBeacon`），**SHALL NOT** 通过 `AbortController` 或等价手段主动掐断 `POST /api/chat/sessions/stream` 的 fetch 响应体以完成停止。

`QaService.stop_chat` SHALL 为停止后的 assistant 消息**唯一权威落库点**，执行顺序 SHALL 包含：flush 流式 `text_buffer`、将 `status=running` 的 tool part 标为 `error` 并写入用户停止相关错误说明、追加用户可读的「本轮已被用户中断」类 text 说明（与 `stream_failure_notice` 结构对齐）、`status=partial`、`extra.finish_reason=stopped`，随后按 `qa_type` 调用对应 Agent/Coordinator 的 `cancel_task`。

当流式协程因 `cancel_task` 收到 `__tw_abort__` 时，SHALL 继续向客户端发送 SSE 直至 `finalize`；若 `user_stopped` 已为真，SHALL **NOT** 再以 `status=completed` 覆盖 `stop_chat` 已写入的 assistant 内容。

#### Scenario: 用户点击停止仅调 stop 且不 abort SSE

- **WHEN** 用户在 chat 页流式生成过程中点击停止按钮
- **THEN** 前端 SHALL 调用 `POST /api/chat/sessions/{session_id}/stop` 且 **SHALL NOT** 对该轮流式 fetch 调用 `abort()`
- **AND** SSE 读循环 SHALL 保持直至收到 `abort` 或 `finish` 及 `data: [DONE]`
- **AND** 前端 SHALL 经 `onFinish` 结束 loading，且 **SHALL NOT** 弹出错误 Toast

#### Scenario: stop 后服务端收敛并落库

- **WHEN** 客户端在流未完成时调用 stop 且存在活跃流式 builder
- **THEN** 服务端 SHALL 调用 `cancel_task` 中止上游生成
- **AND** SHALL 将 assistant 消息以 `status=partial` 落库，且 `extra.finish_reason` SHALL 为 `stopped`
- **AND** 落库内容中 running 的 tool part SHALL 为 `error` 状态且含用户停止语义的错误说明
- **AND** 落库内容 SHALL 含用户可读的「本轮已被用户中断」类说明 text part（无正文时 MAY 仅含该说明）

#### Scenario: stop 后 SSE 正常结束

- **WHEN** `cancel_task` 使上游产出 `__tw_abort__`
- **THEN** SSE SHALL 发出 `abort` 事件
- **AND** 随后 SHALL 发出 `finish`，其 `finish_reason` SHALL 为 `stopped`（或在与 `abort` 组合时仍保证客户端能识别为成功结束）
- **AND** SHALL 以 `data: [DONE]` 收尾

#### Scenario: 页面关闭与用户点击停止同路径

- **WHEN** 用户在流式生成过程中关闭或刷新页面且 `beforeunload` 触发
- **THEN** 客户端 SHALL 通过 `sendBeacon` 或等价方式调用同一 stop 接口
- **AND** 语义 SHALL 与用户点击停止一致（非网络异常通道）

### Requirement: 流式问答与 Langfuse 会话关联（可选）

当 `LANGFUSE_TRACING_ENABLED` 为真时，系统 SHALL 在结构化日志中记录与当次流式请求一致的 `langfuse_session_id`（或等价会话键）以便排障。系统 MAY 在 `POST /api/chat/sessions/stream`（及同会话语义下其它流式端点）的 SSE `data:` JSON 中增加**可选**键（例如面向调试的会话/观测引用），前端对未知键 SHALL 必须忽略而不影响解析。

#### Scenario: 关闭 Langfuse 时不增加字段

- **WHEN** `LANGFUSE_TRACING_ENABLED` 不为真
- **THEN** 流式 SSE 帧的 JSON SHALL 不因本能力增加新的必选或推荐键

#### Scenario: 启用时可选键不破坏协议

- **WHEN** 启用且实现选择向 SSE 透出引用
- **THEN** 该键 SHALL 为可选，且 SHALL NOT 包含 `LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY` 或 JWT 等密钥类内容

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

### Requirement: LLM 工厂 SHALL 按 MODEL_TYPE 选用厂商 LangChain 集成

系统 SHALL 在 `backend/llm/factory.get_llm()` 中根据 `ModelConfig.model_type`（环境变量 `MODEL_TYPE`）实例化聊天模型，且 SHALL 满足：

- `MODEL_TYPE` 为 **`qwen`**（大小写不敏感）时，SHALL 使用 **`langchain-qwq>=0.3.5`** 的 `ChatQwen`（流式 `reasoning_content` 由集成包映射）。
- `MODEL_TYPE` 为 **`deepseek`** 时，SHALL 使用 **`langchain-deepseek`** 的 `ChatDeepSeek`。
- `MODEL_TYPE` 为 **`openai`**、**`minimax`** 或未在上述列表中声明的其它值时，SHALL 使用 **`langchain_openai.ChatOpenAI`**（兼容 OpenAI 协议 base URL）。

未配置的 `MODEL_TYPE` 或缺失必填密钥时，系统 SHALL 按现有约定在启动或首次调用时失败并给出明确错误，不得静默回退到错误厂商类。

#### Scenario: 配置为 qwen 时使用 ChatQwen

- **WHEN** 环境变量 `MODEL_TYPE=qwen` 且 DashScope 相关密钥与 base URL 已配置
- **THEN** `get_llm()` 返回的实例类名或模块路径 SHALL 表明其来自 `langchain_qwq`（或项目子类）

#### Scenario: 配置为 deepseek 时使用 ChatDeepSeek

- **WHEN** 环境变量 `MODEL_TYPE=deepseek` 且 DeepSeek API 密钥已配置
- **THEN** `get_llm()` 返回的实例 SHALL 为 `langchain_deepseek.ChatDeepSeek`（或等价导入路径）

#### Scenario: 配置为 openai 时使用 ChatOpenAI

- **WHEN** 环境变量 `MODEL_TYPE=openai`
- **THEN** `get_llm()` 返回的实例 SHALL 为 `langchain_openai.ChatOpenAI`

### Requirement: LangGraphSseBridge SHALL 从 AIMessageChunk 提取思考并发出 reasoning-* SSE

当 `ModelConfig.show_thinking_process` 为真（默认 `true`，大小写不敏感）时，在 `POST /api/chat/sessions/stream` 及共用 `LangGraphSseBridge` 的其它流式端点中，系统在处理 LangChain `on_chat_model_stream` 事件时 SHALL：

1. 通过统一的 **`extract_reasoning_delta`**（或等价函数）从 `data.chunk`（`AIMessageChunk`）读取思考增量，读取顺序 SHALL 为：`additional_kwargs["reasoning_content"]` → `additional_kwargs["reasoning"]` → 可选的 chunk 级 `reasoning_content` 属性；均为空则视为本 chunk 无思考增量。
2. 对非空思考增量，按 **`reasoning-start` → （一次或多次）`reasoning-delta` → `reasoning-end`** 发出 SSE `data:` JSON，字段 SHALL 与 `docs/prd/platform/SSE流式数据设计.md` 一致（含 `messageId`、`partId`、`textDelta`）。
3. 当同一 chunk 或后续 chunk 出现非空 **正文** `content` 时，SHALL 在发出 `text-delta` 之前结束当前 reasoning 块（`reasoning-end`）。
4. 在 `on_tool_start`、下一轮 `on_chat_model_start`（若实现区分 run）、流正常结束前，SHALL 关闭尚未结束的 reasoning 块。
5. 同步调用 `AssistantMessageBuilder.append_reasoning_delta`（或等价）以写入 `content.parts` 中 `type: "reasoning"` 的片段。

当 `show_thinking_process` 为假时，系统 SHALL NOT 因本 Requirement 发出 `reasoning-start` / `reasoning-delta` / `reasoning-end`，且 SHALL NOT 改变既有 `text-*` 与 `tool-*` 帧。

#### Scenario: Qwen 流式返回 reasoning_content 时出现 reasoning 序列

- **WHEN** `MODEL_TYPE=qwen`，`show_thinking_process` 为真，且上游 `AIMessageChunk.additional_kwargs` 含非空 `reasoning_content`
- **THEN** SSE 输出 SHALL 包含至少一对 `reasoning-start` 与 `reasoning-delta`，且 `reasoning-delta.textDelta` 累积非空

#### Scenario: DeepSeek 流式返回 reasoning_content 时出现 reasoning 序列

- **WHEN** `MODEL_TYPE=deepseek`，`show_thinking_process` 为真，且 chunk 含非空 `reasoning_content`（经 `langchain-deepseek` 映射）
- **THEN** SSE 输出 SHALL 包含 `reasoning-delta` 帧，且 JSON 可被 `useSSEStream` 解析

#### Scenario: 正文开始后关闭 reasoning

- **WHEN** 已发出 `reasoning-start`，随后同一 LLM 轮次出现非空 `content` 增量
- **THEN** 系统 SHALL 在对应 `text-delta` 之前发出 `reasoning-end`，且同一 `partId` 的 reasoning 块不再追加 `reasoning-delta`

#### Scenario: 关闭 show_thinking_process 时不发 reasoning 帧

- **WHEN** `show_thinking_process` 为 `false`
- **THEN** 流式响应 SHALL NOT 包含 `type` 为 `reasoning-start`、`reasoning-delta` 或 `reasoning-end` 的业务帧

### Requirement: 思考提取 SHALL 具备 bridge 层自动化回归

系统 SHALL 为 `LangGraphSseBridge`（或与其共享的 reasoning 发射入口）提供自动化测试：向 `on_chat_model_stream` 合成事件注入带 `additional_kwargs.reasoning_content` 的 mock `AIMessageChunk`，收集输出字符串后 SHALL 断言：

- 存在合法 SSE 帧边界；
- 至少一段 `data:` JSON 的 `type` 为 `reasoning-delta` 且含非空 `textDelta`；
- 在后续注入带 `content` 的 chunk 后，存在 `reasoning-end` 再出现 `text-delta`。

#### Scenario: reasoning golden 断言

- **WHEN** 测试仅传入 reasoning 增量 chunk 后再传入 content chunk
- **THEN** 输出字符串中 `reasoning-delta` SHALL 先于对应 `text-delta` 出现，且 `reasoning-end` 位于二者之间

### Requirement: chat 流式页 SHALL 原生 reasoning 优先于 redacted_thinking 兜底

在 `chat.vue` 流式路径中，当前端经 `useSSEStream` 收到 **`reasoning-delta`**（或 `reasoning-start`）后，本轮 assistant 消息 SHALL 标记为已收到 **原生 reasoning 流**。此后对 **`text-delta`** 的处理 SHALL 使用普通 `appendTextDelta`，**SHALL NOT** 再对同一段 `text-delta` 执行 `<think>` 标签拆分。

若整轮流式回合内 **未** 收到任何 `reasoning-delta`，前端 SHALL 继续对 `text-delta` 使用现有 **`appendTextDeltaWithRedactedThinking`** 兜底逻辑，行为与变更前一致。

`useSSEStream` SHALL 支持处理 `reasoning-start` 与 `reasoning-end`（回调或内部更新 part `status`），使 `ReasoningBlock` 在流式过程中与 `finish` 时状态正确。

#### Scenario: 有 reasoning-delta 时不拆 redacted 标签

- **WHEN** SSE 序列先出现 `reasoning-delta`，后出现含 `<think>` 子串的 `text-delta`
- **THEN** 该 `text-delta` 中的标签子串 SHALL 保留在 `text` part 或按纯文本追加，且 SHALL NOT 再生成额外 `reasoning` part

#### Scenario: 无 reasoning-delta 时仍走标签兜底

- **WHEN** 整轮 SSE 未出现 `reasoning-delta`，且 `text-delta` 含完整 `<think>…</think>` 对
- **THEN** 前端 SHALL 生成 `reasoning` part 并在 `ReasoningBlock` 中展示，与 `docs/dev/redacted-thinking-inline-parsing.md` 一致

#### Scenario: 原生 reasoning 流式展示

- **WHEN** 后端发出 `reasoning-delta` 且 `show_thinking_process` 为真
- **THEN** chat 页 SHALL 渲染 `ReasoningBlock`，且流式过程中标题为「思考中…」类状态直至 `reasoning-end` 或 `finish`

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

### Requirement: chat 页 FileUploadManager SHALL 使用会话附件 API（COMMON_QA 发送时上传）

在 `qa_type=COMMON_QA`（及未来显式启用的 qa_type）下，**发送阶段** SHALL 调用 `POST /api/chat/sessions/{session_id}/attachments` 上传本地队列中的文件；**SHALL NOT** 在用户选文件时立即调用该 API。

**SHALL NOT** 对 COMMON_QA 使用 `/api/knowledge_base/collections/tmp/upload`。

发送前 **SHALL** 通过 `PUT /api/chat/sessions/{session_id}/ensure` 保证 session 已存在，再 upload。Composer 行为详见 **`chat-composer-send-upload`** spec。

`FAULT_OPERATION_QA` SHALL 继续禁止文件上传。

#### Scenario: COMMON_QA 发送时走附件 API

- **WHEN** 用户在智能问答输入文字并附带本地队列中的文档，点击发送
- **THEN** 前端 SHALL 在 stream 之前请求 `POST /api/chat/sessions/{session_id}/attachments`
- **AND** SHALL NOT 在选文件当下请求该 API

#### Scenario: 选文件时不走 KB tmp

- **WHEN** 用户在 COMMON_QA 选择上传文档
- **THEN** 前端 SHALL NOT 请求 `/api/knowledge_base/collections/tmp/upload`

#### Scenario: 故障运维仍禁止上传

- **WHEN** `qa_type=FAULT_OPERATION_QA` 且用户尝试添加附件
- **THEN** 前端 SHALL 阻止进入可发送状态并提示不支持文件上传

### Requirement: 流式请求 extra.file_dict SHALL 为 Dict[str, str]

`POST /api/chat/sessions/stream` 及 `QaQueryRequest.file_dict` SHALL 要求类型为 `Dict[str, str]`：键为原始文件名（显示用），值为 `__CHAT_ATTACHMENT__:<uuid>` 哨兵或内联正文。

前端 chat 页 **SHALL NOT** 将 `file_list` 数组原样作为 `file_dict` 发送。

`file_dict` **SHALL** 在 **全部待发送附件 upload 成功之后**、**SSE stream 启动之前** 由前端组装。

user 消息持久化时 `extra.file_dict` SHALL 保存上述字典格式，以便历史会话恢复。

#### Scenario: file_dict 在 upload 完成后发送

- **WHEN** 用户发送带 `notes.pdf` 的消息且 upload 返回 `attachment_id=abc`
- **THEN** stream 请求 `extra.file_dict` SHALL 为 `{"notes.pdf": "__CHAT_ATTACHMENT__:abc"}`
- **AND** SHALL 在 upload 响应之后发起 stream

#### Scenario: 历史消息恢复附件展示

- **WHEN** 客户端加载含 `extra.file_dict` 的 user 消息
- **THEN** chat 页 SHALL 在用户气泡旁展示附件文件名列表（与现有 `FileListItem` 行为一致）

### Requirement: 系统 SHALL 提供可配置的上下文窗口上限

系统 SHALL 在 `config.yaml` 的 **`context`** 段（经 `ModelConfig` 暴露）支持：

- **`max_input_tokens`**：非负整数，表示 Agent 会话上下文窗口上限（token 估算基准）。当值 **> 0** 时，系统 SHALL 将其作为上下文占用率的分母，且 **`SummarizationOffloadMiddleware`**（及等价摘要逻辑）SHALL 使用同一上限作为 `_get_profile_limits()` 的优先来源。
- **`display_enabled`**：布尔值；为真时系统 SHALL 向 SSE 推送 `context-update` 并在 chat 页展示指示器；为假时 SHALL NOT 推送且前端 SHALL 隐藏指示器。

当 **`max_input_tokens` 为 0** 时，系统 SHALL 尝试从主模型 `profile.max_input_tokens` 解析；若仍不可用，SHALL 记录 warning 并使用仓库约定的保守默认值（SHALL NOT 回退到 `generation.max_tokens`）。

环境变量 MAY 通过既有 `ModelConfig` 合并机制覆盖 yaml（命名以 `design.md` / `config.example.yaml` 为准）。

#### Scenario: 显式配置上下文上限

- **WHEN** `context.max_input_tokens` 设为 `128000` 且 `display_enabled` 为真
- **THEN** `context-update` 帧的 `context.max_tokens` SHALL 为 `128000`
- **AND** 摘要触发分数 SHALL 基于 `128000` 与 `summarization.trigger_fraction` 计算

#### Scenario: 关闭展示时不推送 SSE

- **WHEN** `context.display_enabled` 为假
- **THEN** 流式路径 SHALL NOT 发出 `context-update` 事件
- **AND** chat 页 composer footer SHALL NOT 展示上下文指示器

### Requirement: 流式路径 SHALL 发出会话上下文占用的 context-update

对经 **`create_noesis_agent`** 装配的 Agent 流式路径（至少 **`COMMON_QA`**、**`FAULT_OPERATION_QA`**、**`DEEP_RESEARCH_QA`**），系统 SHALL 在每次 LLM 调用前的 **`before_model`** 阶段估算当前消息列表的输入 token 数，并在 **`context.display_enabled`** 为真时发出 SSE 事件 **`context-update`**。

`data:` JSON SHALL 包含至少：

- **`type`**: `"context-update"`
- **`messageId`**: 当前 assistant 消息 ID（与 `message-start` 一致）
- **`context`**: 对象，键为 **`current_tokens`**（非负整数）、**`max_tokens`**（正整数）、**`used_percentage`**（0–100 整数，`min(100, round(current_tokens / max_tokens * 100))`）

当摘要或工具结果卸载导致消息列表 token 估算变化后，系统 SHALL 在后续 `before_model` 或等价时机再次发出 `context-update`，使 `current_tokens` 反映压缩后的值。

本 Requirement 的 token 估算 SHALL 使用与 **`SummarizationOffloadMiddleware`** 相同的计数器实现，SHALL NOT 使用 provider `usage_metadata` 或 `usage-update` 累计值替代。

`TEST_CASE_QA`（CaseCoordinator StateGraph）路径在本能力首期 MAY 不发出 `context-update`；前端对该 `qa_type` SHALL 隐藏上下文指示器。

#### Scenario: 单轮对话前发出 context-update

- **WHEN** 用户发起流式对话且 `before_model` 估算 `current_tokens=29000`、`max_tokens=128000`
- **THEN** SSE SHALL 含 `context-update`，其 `context` 为 `{ current_tokens: 29000, max_tokens: 128000, used_percentage: 23 }`（四舍五入）

#### Scenario: 摘要后上下文下降

- **WHEN** 摘要中间件将估算 token 从 `90000` 降至 `35000`，且下一次 `before_model` 运行
- **THEN** 随后 `context-update` 的 `current_tokens` SHALL 约为 `35000`，`used_percentage` SHALL 相应下降

#### Scenario: LangGraphSseBridge golden 覆盖 context-update

- **WHEN** 自动化测试向桥接注入含 custom stream `context-update` 负载或等价合成事件
- **THEN** 输出 SHALL 含 `event: context-update` 且 `data` JSON 含 `type`、`messageId`、`context.current_tokens`、`context.max_tokens`、`context.used_percentage`

### Requirement: 会话 SHALL 持久化最近上下文快照

系统 SHALL 在流式过程中或回合结束时，将会话**最近一次**有效的 `context-update` 快照写入该会话的 **`extra.context`**（或与之等价的会话级 JSON 字段），至少包含 **`current_tokens`**、**`max_tokens`**、**`used_percentage`**。

加载会话历史时，客户端 SHALL 可从会话 `extra.context` 恢复 composer footer 指示器的初值；流式 `context-update` 到达后 SHALL 覆盖本地状态。

#### Scenario: 刷新页面后恢复指示器

- **WHEN** 用户刷新 chat 页且会话 `extra.context` 为 `{ current_tokens: 87040, max_tokens: 128000, used_percentage: 68 }`
- **THEN** composer footer SHALL 展示约 `68%` 的环形指示器
- **AND** hover tooltip SHALL 展示 `87K / 128K` 量级绝对值（格式化规则见前端 Requirement）

### Requirement: chat 页 composer footer SHALL 展示上下文占用指示器

系统 SHALL 在 **`chat.vue`** 输入区（composer）**下方**展示会话级上下文指示器，行为如下：

- 默认展示 **环形进度 + 整数百分比**（如 `68%`），数据来源于最新 `context-update` 或会话 `extra.context`。
- 用户 **hover** 指示器时，SHALL 通过 tooltip 展示 **绝对 token 值**，格式为 **`{current} / {max}`**，使用与 `formatTokenCount` 一致的缩写（如 `87K / 128K`）。
- 指示器 SHALL 与 assistant 气泡底部的 **`usage-update` 计费 token 行分开**，SHALL NOT 合并为一行。
- 当无有效 `context` 数据（`max_tokens` 缺失或 `display_enabled` 为假，或 `qa_type` 为 `TEST_CASE_QA`）时，SHALL 隐藏指示器。
- 流式过程中 SHALL 随 `context-update` 实时刷新；未知 SSE 键 SHALL 兼容忽略。

颜色语义 SHALL 随 `used_percentage` 变化：低于 60% 为中性、60%–84% 为警告色、85% 及以上为接近上限色（与默认摘要触发比例 0.85 对齐）。

#### Scenario: hover 显示绝对值

- **WHEN** 指示器显示 `68%` 且 `context` 为 `{ current_tokens: 87040, max_tokens: 128000 }`
- **THEN** 用户 hover 时 tooltip SHALL 展示 `87K / 128K`（或等价格式化结果）

#### Scenario: 与 usage 行并存

- **WHEN** 同一会话 assistant 消息含 `usage-update` 累计 token 且 composer footer 含上下文指示器
- **THEN** assistant 气泡底部 SHALL 仍展示 `↑… ↓…` 计费摘要
- **AND** composer footer SHALL 独立展示上下文百分比，二者 SHALL NOT 互相替代

#### Scenario: TEST_CASE_QA 隐藏指示器

- **WHEN** 用户切换 `qa_type` 为 `TEST_CASE_QA`
- **THEN** composer footer SHALL NOT 展示上下文指示器

### Requirement: 用户停止、网络中断与生成失败 SHALL 分流

系统 SHALL 将流式对话的异常终态分为三条互不复用的路径：**用户主动停止**（`/stop`）、**网络或连接中断**（未调用 `/stop` 的断连）、**生成过程失败**（Agent/桥接层错误）。各路径在前端回调、Toast、assistant 落库文案与 `extra.finish_reason` 上 SHALL 保持可区分。

用户主动停止 SHALL 使用 `finish_reason=stopped` 与用户中断说明；SHALL NOT 使用 `onError` 或连接类错误 Toast。

网络或连接中断 SHALL NOT 调用 `/stop`；服务端 `CancelledError` 落库 SHALL 为 `status=partial` 且 **SHALL NOT** 写入用户中断说明；前端 SHALL 经 `onError` 展示「网络异常，请稍后重试」类 Toast，且对连接类错误 **SHALL NOT** 在气泡内追加长段失败说明（与现有 `isConnectionOrTimeoutError` 策略一致）。

生成过程失败 SHALL 继续发出 SSE `error` 与 `finish`（`finish_reason=error`）；落库与展示 SHALL 使用 `append_stream_failure_notice` / `appendStreamFailureNotice` 语义，且 **SHALL NOT** 使用 `finish_reason=stopped`。

#### Scenario: 网络断开不走用户停止文案

- **WHEN** 流式过程中发生网络断开或 `fetch`/`read` 失败，且客户端未调用 `/stop`
- **THEN** 前端 SHALL 调用 `onError` 并展示网络类 Toast
- **AND** 服务端若因 `CancelledError` 落库，assistant `extra.finish_reason` SHALL NOT 为 `stopped`
- **AND** 落库内容 SHALL NOT 含「本轮已被用户中断」类说明

#### Scenario: 生成失败不走用户停止

- **WHEN** Agent 或桥接层发出 `error` 且 `finish_reason=error`
- **THEN** 前端 SHALL 调用 `onError` 并追加生成失败说明（非用户中断文案）
- **AND** `extra.finish_reason` SHALL 为 `error` 或等价错误标记，SHALL NOT 为 `stopped`

#### Scenario: tool call 阶段用户停止后历史可辨识

- **WHEN** 用户在 assistant 已发出 tool 调用但尚无 tool 输出时调用 `/stop`
- **THEN** 落库的 tool part SHALL 为 `error` 且 SHALL NOT 保持 `running` 或误标为 `success`
- **AND** 助手消息 SHALL 含用户中断说明，使用户刷新会话后不会看到「工具成功但无输出」的歧义状态

### Requirement: chat 页停止 UI SHALL 等待服务端 SSE 结束

在采用后端单一停止路径后，`chat.vue` 的 `stopChatStream`（或等价函数）SHALL 调用 `stopChat` API 后保持 `stylizingLoading`（或等价加载态）直至 `useSSEStream` 的 `onFinish` 触发；SHALL NOT 在调用 stop 后立即本地将 running 工具标为 `success`。

收到 `finish` 且 `finish_reason=stopped` 时，前端 MAY 调用与后端文案对齐的 `appendUserStopNotice` 以同步当前气泡；历史消息加载时若 `extra.finish_reason=stopped` 且 parts 尚无中断说明，SHALL 补全展示。

#### Scenario: 停止后 loading 直至 SSE 收尾

- **WHEN** 用户点击停止且 `/stop` 请求已发出
- **THEN** 输入区加载态 SHALL 保持直至 SSE `onFinish`
- **AND** SHALL NOT 依赖 `AbortError` 结束加载

#### Scenario: 历史消息恢复用户停止状态

- **WHEN** 客户端加载 `status=partial` 且 `extra.finish_reason=stopped` 的 assistant 消息
- **THEN** chat 页 SHALL 展示用户中断说明与 error 态的未完成工具
- **AND** SHALL NOT 将未完成工具展示为成功

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

### Requirement: 平台 SHALL 提供会话上下文浏览 API

系统 SHALL 在 `chat` 路由下提供 `GET /api/chat/sessions/{session_id}/context` 与 `GET /api/chat/sessions/{session_id}/workspace/file`，行为遵循 `chat-session-context-panel` 规格。

#### Scenario: context API 注册

- **WHEN** 应用启动并加载 `chat_api` 路由
- **THEN** OpenAPI **SHALL** 包含上述两个 GET 端点

### Requirement: 删会话 SHALL 清理统一用户会话子树

用户软删或批量删除本人会话时，`ChatService` 在完成会话与消息软删后 **SHALL** 调用 `user_data_paths.delete_session_data(user_id, session_id)`，删除 `.data/users/{user_id}/sessions/{session_id}/` 整棵子树（含 `workspace/`、`uploads/`、`attachments/`；详见 `agent-runtime-paths`）。

删会话 **SHALL NOT** 调用 `destroy_user_sandbox`；**SHALL NOT** 提供跳过磁盘清理的配置开关。

#### Scenario: 删他人会话不清理磁盘

- **WHEN** 用户 A 尝试删除用户 B 的会话
- **THEN** 系统 SHALL 返回 404 或等价未授权语义，**SHALL NOT** 删除用户 B 的会话子树

#### Scenario: 单会话删除清磁盘

- **WHEN** 用户 `DELETE /api/chat/sessions/{session_id}` 成功
- **THEN** `.data/users/{uid}/sessions/{session_id}/` SHALL 不再存在于磁盘

#### Scenario: 批量删除

- **WHEN** 用户 `POST /api/chat/sessions/batch-delete` 成功删除多个会话
- **THEN** 每个对应 `sessions/{session_id}/` 子树 SHALL 被删除

