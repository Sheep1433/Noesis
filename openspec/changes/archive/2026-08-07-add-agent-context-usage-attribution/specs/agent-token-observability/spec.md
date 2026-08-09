## ADDED Requirements

### Requirement: 当前上下文快照 SHALL 基于最终模型请求

系统 SHALL 在每次模型调用前基于所有 Agent middleware 处理完成后的最终 `ModelRequest` 生成当前上下文快照。快照 SHALL 至少包含当前估算 token、模型上下文上限、占用比例，以及 system、conversation、tool results、tool definitions、other 顶层分类；快照 SHALL 表示单次即将发送的请求，SHALL NOT 累加为 run usage。

#### Scenario: 工具定义计入当前上下文
- **WHEN** 最终模型请求包含对话消息和已绑定工具定义
- **THEN** `current_tokens` SHALL 覆盖消息与工具定义
- **AND** breakdown SHALL 单独提供 `tool_definitions`

#### Scenario: 多次模型调用只更新当前快照
- **WHEN** 同一 run 依次进行两次模型调用
- **THEN** 当前 context 展示 SHALL 使用最后一次调用的快照
- **AND** SHALL NOT 将两次 `current_tokens` 相加

### Requirement: 上下文来源细分 SHALL 依赖可靠 provenance

系统 SHALL 支持 Skills、memory、RAG、attachments 等来源细分。来源注入方 SHALL 使用不进入 Provider 输入的内部 provenance 标记实际注入内容；统计器 SHALL 仅根据显式标记或已解析的权威工具路径归属来源，SHALL NOT 通过任意正文正则猜测。缺少可靠标记的内容 SHALL 保留在顶层分类或 `other`。

#### Scenario: Skills 列表被标记
- **WHEN** Skills middleware 将可用 Skills 列表注入最终 system message 并提供 provenance
- **THEN** 快照 SHALL 在 system 总量内报告 `sources.skills`
- **AND** 分类总量 SHALL NOT 因同一内容同时属于 system 与 Skills 而重复计入 `current_tokens`

#### Scenario: 未标记工具结果安全降级
- **WHEN** ToolMessage 没有可验证的来源标记
- **THEN** 其 token SHALL 计入 `tool_results`
- **AND** 系统 SHALL NOT 猜测其属于 RAG、Skills 或 attachments

### Requirement: 上下文分类 SHALL 明确估算语义

系统 SHALL 将本地上下文分类标记为估算，并记录可用的计数方法。分类 SHALL 使用一致的计数路径；本地序列化或 framing 差值 SHALL 进入 `other` 或等价未归属字段。系统 SHALL NOT 按比例改写分类以冒充 Provider 实际 input usage。

#### Scenario: Provider input 与本地估算不同
- **WHEN** Provider 返回的 `input_tokens` 与本地 `current_tokens` 不一致
- **THEN** 系统 SHALL 保留两个原始值及其不同语义
- **AND** SHALL NOT 强制修改各分类使二者相等

### Requirement: Provider usage SHALL 保留可用明细

系统 SHALL 规范化每次模型响应的 input、output、total token，并在 Provider 提供时保留 cache read、cache write、reasoning 等 detail。缺失的 detail SHALL 表示为不可用，SHALL NOT 默认伪造为零。detail SHALL 作为后端规范化与按需调试展示字段保留，SHALL NOT 作为 chat 页默认 token 摘要展示项。

#### Scenario: Responses API 返回 cache 与 reasoning
- **WHEN** Provider usage 含 cached input tokens 与 reasoning output tokens
- **THEN** 规范 usage SHALL 保留对应 `input_token_details` 与 `output_token_details`
- **AND** total token SHALL NOT 因 detail 再次重复相加
- **AND** chat 页默认摘要 SHALL NOT 展示 cache/reasoning，仅按需调试视图可读取

#### Scenario: Provider 只返回基础 usage
- **WHEN** Provider 只返回 input、output 和 total
- **THEN** 基础 usage SHALL 正常展示
- **AND** cache/reasoning SHALL 显示不可用或省略

### Requirement: Run usage SHALL 按 caller 和模型调用归属

系统 SHALL 将一轮 Agent run 的实际 Provider usage 按唯一 model run id 去重累计，并至少支持 `lead_agent`、`subagent`、`middleware` caller。系统 SHALL 支持按模型汇总，并为调试保留有界 step attribution；子 Agent usage SHALL 只计入 run 总量一次。

#### Scenario: 主 Agent 与子 Agent 分别调用模型
- **WHEN** 一轮 run 中主 Agent 和子 Agent 各完成一次模型调用
- **THEN** run cumulative SHALL 等于两次实际 usage 之和
- **AND** `by_caller` SHALL 分别报告 `lead_agent` 与 `subagent`

#### Scenario: 重复模型完成事件
- **WHEN** 同一 model run id 因流式与终态事件被观察两次
- **THEN** usage SHALL 只累计一次

#### Scenario: 调试步骤数量受限
- **WHEN** 长时间 Agent run 产生大量模型与工具事件
- **THEN** step attribution SHALL 按配置上限或语义完成事件有界保存
- **AND** SHALL NOT 按每个 token delta 生成 attribution 记录

### Requirement: 内部 attribution 元数据 SHALL NOT 进入模型输入

用于 token 来源和 caller 归属的 Noesis 内部元数据 SHALL 保持 request/run scoped，并在 Provider wire payload 生成前被剥离或位于不可序列化的内部上下文。该元数据 SHALL NOT 改变 prompt 文本、tool schema 或 prompt cache key。

#### Scenario: 检查 Provider 请求载荷
- **WHEN** 带 Skills、memory 和 RAG provenance 的请求被序列化给 Provider
- **THEN** wire payload SHALL 只包含原有模型输入
- **AND** SHALL NOT 出现 Noesis attribution 调试字段

