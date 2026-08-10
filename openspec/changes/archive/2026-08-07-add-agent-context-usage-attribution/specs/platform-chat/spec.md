## MODIFIED Requirements

### Requirement: usage-update 与上下文指示

流式路径 SHALL 发出消息级累计 LLM token 的 `usage-update` 与 `finish.usage`，并 SHALL 在可获得快照时发出当前模型请求占用的 `context-update`。系统 SHALL 提供可配置的上下文窗口上限；会话 MAY 持久化最近上下文快照。

`usage-update` 与 `finish.usage` SHALL 保留 `input_tokens`、`output_tokens`、`total_tokens` 既有字段，并在可用时以向后兼容字段提供 `by_caller` 与 `by_model` 归属；Provider cache/reasoning details SHALL 在后端规范化保留并可经 SSE 字段传递，但 SHALL NOT 作为默认前端摘要展示项。`context-update` SHALL 保留 `current_tokens`、`max_tokens`、`used_percentage`，并以向后兼容字段提供估算标识、顶层 breakdown 和可靠来源细分。

chat 页 SHALL 将“最新模型请求的当前 context window”与“本轮 Agent run 累计实际 usage”分开展示。默认摘要 SHALL 只展示 input/output 与 caller/model 归属；cache/reasoning 等 Provider 明细 SHALL 仅在按需调试视图中展示。旧事件或部分字段缺失时 SHALL 降级到既有总量，SHALL NOT 阻断流式回答。

#### Scenario: finish 含 usage
- **WHEN** 一轮正常完成
- **THEN** `finish`（或等价）SHALL 含累计 usage，供 chat 页展示

#### Scenario: context 与累计 usage 分开展示
- **WHEN** 一轮 Agent run 已进行多次模型调用并收到最新 context 快照
- **THEN** chat 页 SHALL 将最新 context 占用与多次调用累计 usage 分开展示
- **AND** SHALL NOT 用累计 input token 作为当前 context window 占用

#### Scenario: 新字段向后兼容
- **WHEN** 客户端只识别既有 usage/context 总量字段
- **THEN** 扩展后的 SSE 事件 SHALL 仍可被该客户端消费
- **AND** 服务端 SHALL NOT 删除或重命名既有字段

#### Scenario: Provider 明细缺失
- **WHEN** Provider 未返回 cache、reasoning 或 caller 调试明细
- **THEN** chat 页 SHALL 继续展示可用的 input/output/total 与 context 总量
- **AND** SHALL NOT 将缺失明细显示为确定的零消耗
