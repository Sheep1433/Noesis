## ADDED Requirements

### Requirement: Tool failure 语义 SHALL 接入统一 Tool Result Envelope

现有 `status`、`errorCategory` 与 `outcome` 语义 SHALL 作为 Tool Execution 内部 result envelope 的字段继续保留。调用异常 SHALL 先按现有 typed exception 规则分类，再由统一 Tool Execution 产生 error ToolMessage；有界化 SHALL 在分类之后执行，且 SHALL NOT 把错误正文截断成 success。

#### Scenario: 大型错误详情仍保持 error

- **WHEN** 工具抛出带有超长技术详情的 `ToolInfrastructureError`
- **THEN** Agent 侧结果 SHALL 保持 `status=error` 与对应 category
- **AND** 用户可见错误 SHALL 继续使用脱敏短句

### Requirement: 工具结果有界化 SHALL 不改变原始 Outcome

工具正文被 DeepAgents offload 或 Noesis fallback 截断时，`status`、`errorCategory`、`outcome`、exit code 与 timed-out 语义 SHALL 保持不变。Tool Execution SHALL 使用处理后的 ToolMessage content，SHALL NOT 通过解析第三方提示文案重建 artifact 字段。

#### Scenario: command_failed 输出被有界化

- **WHEN** `execute` 返回非零退出且 stdout/stderr 超过单结果预算
- **THEN** result SHALL 仍为 `status=success`、`outcome=command_failed`
- **AND** 有界 content SHALL 保留退出语义与可用的 stderr/stdout
