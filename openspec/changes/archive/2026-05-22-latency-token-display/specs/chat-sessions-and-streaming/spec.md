## ADDED Requirements

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

在 **`chat.vue`**（及共用 **`MessagePartsRenderer`** 的 assistant 渲染路径）中：

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

## MODIFIED Requirements

### Requirement: SSE 对外契约 SHALL 具备自动化回归覆盖

系统 SHALL 为 `LangGraphSseBridge`（或统一的 SSE 字符串格式化入口）提供自动化测试，覆盖至少：`message-start` 形态、`text-delta` 含 `textDelta`、`finish` 含 `finishReason`/`usage`、`data: [DONE]` 收尾；断言方式为解析 `data:` 行 JSON 键集合或关键字段类型，防止静默破坏 `useSSEStream` 消费者。**对测试用例阶段事件，SHALL** 在向桥接入参的路径上包含对 `phase-start`、`phase-delta`、`phase-end` 等合成事件的 **`type`/`phaseId`/`messageId` 与必选键**断言（如 `phase-end` 含 `ok`），帧边界规则与既有 golden 断言一致。

**对本变更新增**：golden 或等价测试 SHALL 覆盖 **`tool-output-available.durationMs`** 类型、**`usage-update`** 帧的 `type`/`messageId`/`usage` 键，以及多轮 LLM 累计后 **`finish.usage`** 与最后一次 **`usage-update`** 一致。

#### Scenario: 桥接层最小 golden 断言

- **WHEN** 测试向桥接传入代表「消息开始」与「文本增量」的合成事件并收集输出字符串
- **THEN** 输出 SHALL 包含合法 SSE 帧边界且 `data:` 负载可被 `json.loads` 解析，且 `type` 字段与事件名一致

#### Scenario: 测试用例 phase 帧 golden 断言

- **WHEN** 测试向与实际流式编码一致的入口传入 `phase-start`、同 `phaseId` 的 `phase-delta` 与 `phase-end`（ok 为布尔）的合成 dict 并串联输出字符串
- **THEN** 收集到的输出字符串 SHALL 满足合法 SSE 帧边界，每一段业务 `data:` SHALL 可被 `json.loads` 解析，且 SHALL 包含与约定一致的 `type`、`phaseId` 及 `phase-end.ok`

#### Scenario: 工具耗时与 usage-update golden 断言

- **WHEN** 测试向桥接传入配对的 `on_tool_start`/`on_tool_end` 及含 `usage_metadata` 的 `on_chat_model_end` 合成事件并收集输出
- **THEN** 输出 SHALL 含 `tool-output-available` 且 `durationMs` 为非负整数，SHALL 含 `usage-update` 且 `usage.input_tokens`/`usage.output_tokens` 为数值，且最终 `finish.usage` SHALL 与累计一致
