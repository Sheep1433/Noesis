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

#### Scenario: 创建或复用会话

- **WHEN** 流式或非流式请求携带 `chat_id`（或等价 session 标识）
- **THEN** 系统 SHALL 在不存在时创建会话，存在时关联同一会话，并在 `extra` 等字段中维护 `qa_type` 等元数据

### Requirement: 消息列表与详情

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/messages` 与 `GET /api/chat/messages/{message_id}`，返回角色、multipart 内容、状态与时间戳，且用户仅能访问本人会话下的消息。

#### Scenario: 越权访问

- **WHEN** 用户请求不属于自己会话的消息或会话
- **THEN** 系统 SHALL 返回 404 或业务约定的无权限结果，且不得泄漏其他用户内容

### Requirement: 流式问答与 SSE 核心契约

系统 SHALL 通过 `POST /api/chat/sessions/stream`（及设计文档中约定的同前缀端点）以 `text/event-stream` 输出 Noesis SSE 帧；事件流由 `LangGraphSseBridge` 从 LangGraph `astream_events` 转换，包含推理与文本增量、工具调用与输出、错误与结束标记，并以 `data: [DONE]` 收尾。

各 `qa_type` 的上游 Agent 选择与场景专属业务帧 **SHALL** 遵循「qa_type 路由」Requirement 及对应 `agent-*` spec；本 Requirement 仅规定平台级帧形态与持久化骨架。

#### Scenario: 用户消息持久化

- **WHEN** 流式连接建立且用户问题非空
- **THEN** 系统 SHALL 在生成开始前持久化 user 角色消息，并在流开始前插入 assistant 骨架行（`status=streaming` 等与实现一致）

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

golden 或等价测试 **SHALL** 覆盖 **`tool-output-available.durationMs`** 类型、**`usage-update`** 帧的 `type`/`messageId`/`usage` 键，以及多轮 LLM 累计后 **`finish.usage`** 与最后一次 **`usage-update`** 一致。

场景专属业务帧（如 `TEST_CASE_QA` 的 `phase-*`）的 golden 断言 **SHALL** 在对应 `agent-*` spec 中规定，本 Requirement **不**要求平台 golden 覆盖 `phase-*`。

#### Scenario: 桥接层最小 golden 断言

- **WHEN** 测试向桥接传入代表「消息开始」与「文本增量」的合成事件并收集输出字符串
- **THEN** 输出 SHALL 包含合法 SSE 帧边界且 `data:` 负载可被 `json.loads` 解析，且 `type` 字段与事件名一致

#### Scenario: 工具耗时与 usage-update golden 断言

- **WHEN** 测试向桥接传入配对的 `on_tool_start`/`on_tool_end` 及含 `usage_metadata` 的 `on_chat_model_end` 合成事件并收集输出
- **THEN** 输出 SHALL 含 `tool-output-available` 且 `durationMs` 为非负整数，SHALL 含 `usage-update` 且 `usage.input_tokens`/`usage.output_tokens` 为数值，且最终 `finish.usage` SHALL 与累计一致

### Requirement: tool-output-available SHALL 携带单次工具调用耗时

系统在 `LangGraphSseBridge` 处理 `on_tool_end` 或 `on_tool_error` 并发出 **`tool-output-available`** 时，SHALL 在 `data:` JSON 中增加可选数值字段 **`durationMs`**，表示自对应 **`on_tool_start`**（或同 **`toolCallId`** 的开始时刻）至工具结束的服务端耗时，单位为毫秒，为非负整数。

同一 **`toolCallId`** 的工具 part 在 reduce 至 assistant **`content.parts`** 时 SHALL 持久化等价的 **`durationMs`** 字段，供历史消息渲染。

前端对缺失 **`durationMs`** 的帧或 part SHALL 兼容忽略，不得导致解析或渲染失败。

#### Scenario: 工具成功结束携带耗时

- **WHEN** Agent 执行一次工具调用，bridge 收到配对的 `on_tool_start` 与 `on_tool_end`
- **THEN** 发出的 `tool-output-available` SHALL 含 `durationMs`，且该 tool part 落库后 SHALL 含相同语义的非负整数

#### Scenario: 工具错误结束仍记录耗时

- **WHEN** bridge 收到 `on_tool_error` 而非 `on_tool_end`
- **THEN** 发出的 `tool-output-available`（`status=error`）SHALL 仍含 `durationMs`（若可计算）

#### Scenario: 历史消息恢复工具耗时

- **WHEN** 客户端加载 assistant 消息，其 `content.parts` 中某 tool part 含 `durationMs: 1200`
- **THEN** chat 页 SHALL 在对应工具折叠块展示约 1.2s 量级耗时，无需依赖 SSE 重放

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

- 当 tool part 或流式 **`tool-output-available`** 含 **`durationMs`** 时，前端 SHALL 在 **`ToolCallCollapse`**（及 **`SubagentCollapse`** 等等价 tool 展示）header 区域展示格式化的单次工具耗时（如秒级 `1.2s`）。
- 当收到 **`usage-update`** 或 **`finish`** 且 **`usage`** 含有效 token 计数时，前端 SHALL 在当前 assistant 消息底部展示**累计** token 摘要（至少区分 input 与 output；有 total 时可一并展示）。
- 展示 SHALL 使用**累计语义**（整条 assistant 回复），SHALL NOT 将单次 LLM 轮次用量作为最终唯一展示（除非该回合仅一轮且与累计相同）。
- 流式过程中 SHALL 随 **`usage-update`** 更新显示；**`finish`** 后定格。加载历史时 SHALL 从 **`message.extra.usage`** 与 **`parts[].durationMs`** 恢复，无需 SSE 重放。

前端对未知 SSE 键或缺失字段 SHALL 兼容忽略。

#### Scenario: 流式多工具多轮 LLM 的可视化

- **WHEN** 用户于 chat 页发起流式对话，SSE 含两次 `tool-output-available`（各带 `durationMs`）及两次 `usage-update`（第二次累计大于第一次），最后 `finish`
- **THEN** 界面 SHALL 展示两个工具各自的耗时，且 token 行 SHALL 显示第二次 `usage-update` 的累计值并在 finish 后保持不变

#### Scenario: 无 token 数据时不占位

- **WHEN** 一次流式回合的 `finish.usage` 为空或无有效 token 字段
- **THEN** assistant 消息 SHALL 不展示 token 摘要行（或等价隐藏），工具耗时仍按 part 正常展示

### Requirement: 停止生成

系统 SHALL 提供 `POST /api/chat/sessions/{session_id}/stop`，使进行中的流式任务可被取消或收敛，并尽可能将已生成的 assistant 内容以一致的多部分格式落库。

#### Scenario: 用户主动停止

- **WHEN** 客户端在流未完成时调用 stop
- **THEN** 服务端 SHALL 中止后续 token 输出，并结束 SSE 或返回明确状态，避免悬挂连接

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
