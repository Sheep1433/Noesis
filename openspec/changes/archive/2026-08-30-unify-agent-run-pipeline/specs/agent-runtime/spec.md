Delta: agent-runtime — 统一 Run 管道

## ADDED Requirements

### Requirement: Run 管道 SHALL 为主/子 Agent 唯一执行内核

系统 SHALL 以单一 RunPipeline 承载 run 执行映射：LangGraph 流事件 → 领域事件（RunStarted / ReasoningDelta / TextDelta / ToolCallStarted / ToolOutputAvailable / UsageDelta / ContextSnapshot / ApprovalRequired / RunFinished）、usage 累计、终态 payload 构造（content + usage + finish_reason）与上下文快照提取。主 Agent（HTTP/SSE 请求作用域）与后台子 Agent（executor 生命周期包装）SHALL 消费同一管道实现；同一 run 级能力 SHALL NOT 在主/子链路存在两份实现。推理档位 ContextVar 的设置点 SHALL 位于管道入口（turn 参数），主链路现状行为不变、子 Agent followup turn 随参数生效。

#### Scenario: 子 Agent run 的 usage 落库

- **WHEN** 后台子 Agent 完成一个 turn
- **THEN** 该 turn 的 usage（steps / llm_ms / input_tokens / output_tokens / cache_read_tokens / cache_write_tokens）SHALL 写入子会话 assistant 消息 `extra.usage`，字段结构与主链路一致
- **AND** 父会话当轮 assistant 消息 SHALL 继续按「主+子合并」口径累计 usage

#### Scenario: 上下文快照单一来源

- **WHEN** 子 Agent turn 内模型调用返回 usage
- **THEN** 上下文快照 SHALL 由管道写入 ContextMetricsRegistry（按 session_id 分键，主子隔离）
- **AND** 代码库中 SHALL NOT 存在第二份针对子 Agent 的快照提取实现

#### Scenario: 主链路 SSE 帧兼容

- **WHEN** 主 Agent run 经统一管道执行并序列化为 SSE
- **THEN** 帧集合、语义与顺序 SHALL 与统一前逐帧一致（reasoning-* / text-* / tool-* / token-details / context-update / finish / [DONE]）

#### Scenario: 推理档位随 turn 生效

- **WHEN** 子 Agent followup turn 携带 reasoning_effort 参数
- **THEN** 该 turn 的模型调用 SHALL 使用指定档位
- **AND** 未指定时 SHALL 继承任务创建时的档位

#### Scenario: 双轨不可表达

- **WHEN** 开发者向 run 管道新增能力（新事件类型、新 usage 字段、新终态属性）
- **THEN** 该能力 SHALL 经由 RunPipeline 单点生效于主/子两条链路，无需分别实现
