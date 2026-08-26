## MODIFIED Requirements

### Requirement: 调用失败 SHALL 转为可续推的 error ToolMessage

工具调用异常（LangGraph 控制异常与整轮取消除外）SHALL 使用权威分类规则生成与原 tool call id 配对的 `status=error` ToolMessage。工具 adapter 在能够确定失败原因时 SHALL 主动抛 typed failure。通用异常翻译 SHALL NOT 同时管理 run budget、subagent scope、telemetry 或 artifact lifecycle。

#### Scenario: Graph 控制异常不被吞掉

- **WHEN** 工具调用抛出 LangGraph 控制异常或整轮取消
- **THEN** 工具错误处理 SHALL 原样传播
- **AND** 若会话随后恢复，message canonicalization SHALL 在再次调用模型前修复不完整配对

#### Scenario: Typed Failure 可续推

- **WHEN** MCP、Web、KB 或 filesystem tool 抛出 `ToolFailureError`
- **THEN** 系统 SHALL 返回同 tool call id 的 error ToolMessage
- **AND** 模型 SHALL 能在合法 tool call/result history 上继续决策

### Requirement: Tool failure 语义 SHALL 接入统一 Tool Result Envelope

`status`、`errorCategory` 与 `outcome` SHALL 继续作为 ToolMessage、RunEvent 与 assistant part 的稳定字段，但 SHALL 直接来源于最终工具结果，不要求额外全局 envelope。Typed failure 在异常翻译边界确定；command outcome 在具体 tool adapter 确定；输出 bounding 不得改变二者语义。Stream 映射 SHALL 读取已有字段，不得重新猜测 typed category。

#### Scenario: 大型错误详情仍保持 Error

- **WHEN** 工具抛出带超长内部详情的 `ToolInfrastructureError`
- **THEN** 最终 ToolMessage SHALL 保持 `status=error` 与对应 category
- **AND** 用户可见 error SHALL 继续使用固定脱敏短句

#### Scenario: Command Failure 仍是 Outcome

- **WHEN** execute 正常返回非零 exit code
- **THEN** SHALL 保持 `status=success` 与 `outcome=command_failed`
- **AND** 通用 failure 处理 SHALL NOT 将其重新分类为调用异常

## ADDED Requirements

### Requirement: Tool Result Budget SHALL 产生确定性 Replacement

工具结果在写入 effective history 前 SHALL 先由工具源或 Filesystem artifact 机制处理；仍超限时 SHALL 生成包含 artifact path/reference、synopsis、原内容 hash 和 replacement reason 的有界结果。Replacement SHALL 保留原 `status`、`errorCategory`、`outcome` 和 tool call id，并 SHALL 在 checkpoint resume 后重放同一决策。

#### Scenario: 恢复已替换的大结果

- **WHEN** 包含大 ToolMessage 的 run 从 checkpoint 恢复
- **THEN** 有效 history SHALL 继续使用原 replacement record
- **AND** SHALL NOT 重新转存、重新摘要或将 error 改为 success
