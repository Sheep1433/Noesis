## ADDED Requirements

### Requirement: Research activity events SHALL be optional and run-scoped

当 `SUPER_AGENT_QA` research context 激活时，Delivery MAY 发出可选 research activity 事件。事件 SHALL 携带 `run_id`、sequence、research_run_id、activity_id、phase 和状态；普通客户端忽略新增字段或事件时 SHALL 不影响现有聊天流。

#### Scenario: Research progress event

- **WHEN** research phase、candidate batch、evidence validation 或 task activity 发生语义状态变化
- **THEN** 服务端 MAY 发出对应 activity 事件，并 SHALL 保持既有 tool/reasoning/text 事件顺序和终态语义

#### Scenario: Client does not support research events

- **WHEN** 客户端只识别现有聊天 SSE 事件
- **THEN** 客户端 SHALL 仍能通过 run snapshot、assistant parts 和 finish 完成渲染

### Requirement: Research trace updates SHALL be idempotent

同一 research activity、candidate、evidence 或 citation 的重放、重订阅和 checkpoint 恢复 SHALL 使用稳定 identity 幂等合并，SHALL NOT 重复创建来源或重复绑定引用。

#### Scenario: SSE resubscription

- **WHEN** 客户端因 sequence gap 重新订阅 research run
- **THEN** 服务端 SHALL 通过 snapshot 或后续事件恢复同一 trace identity，客户端 SHALL NOT 展示重复来源
