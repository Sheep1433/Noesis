## ADDED Requirements

### Requirement: Agent 离线评测经 harness

`evals.agent`（含 Harbor）SHALL 通过 `noesis` 包的工厂与流式核执行被测 Agent。**SHALL NOT** 在 worker 内长期维护与线上分叉的完整 middleware/factory 装配副本（允许注入评测专用 backend / prompt / collector）。

#### Scenario: Harbor 使用 harness factory

- **WHEN** 运行 Harbor `noesis_worker`
- **THEN** SHALL `from noesis.factory import create_noesis_agent` 并经 `noesis.runtime.stream.stream_agent_events` 消费事件

#### Scenario: Harbor 不加载平台 wiring

- **WHEN** Harbor worker 初始化内存 checkpointer 并执行无 KB/附件工具的 Agent
- **THEN** SHALL NOT import `noesis_server.services.harness_wiring`，也 SHALL NOT 初始化平台 KB、附件或 ORM 服务

## REMOVED Requirements

### Requirement: Agent 离线评测

**Reason**: 旧 requirement 只约束可重复运行和 profile，无法保证评测与线上复用同一 harness 装配与流式核。

**Migration**: 使用新增的「Agent 离线评测经 harness」requirement。
