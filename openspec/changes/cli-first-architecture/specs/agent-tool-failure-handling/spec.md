# Delta: agent-tool-failure-handling — 适用范围收窄

## MODIFIED Requirements

### Requirement: 本规格 SHALL 限定适用范围

本规格 **SHALL** 仅约束同时满足下列条件的工具结束路径：

1. 上游为 LangChain `astream_events` 的 `on_tool_end` / `on_tool_error`；
2. 经事件映射层发出 `tool-output-available`；
3. 工具调用链装配了 `ToolFailureMiddleware`（经 `create_noesis_agent` 的 Agent 路径）。

**In scope**：`COMMON_QA` 与 `SUPER_AGENT_QA` 的 ReAct Agent 工具调用（`FAULT_OPERATION_QA` 已下线，见 `agent-profiles`）。**Out of scope**：整轮 SSE `error`（由 `sanitize_stream_error` 处理，见分流 Requirement）；前端 UI 标签与占位文案（权威来源：`platform-chat`）。

#### Scenario: in-scope 路径的判定

- **WHEN** 目标 Agent（经 `create_noesis_agent` 装配 `ToolFailureMiddleware`）的工具调用以 `on_tool_end` / `on_tool_error` 结束并映射为 `tool-output-available`
- **THEN** 该路径 SHALL 由本规格约束双层语义与 `state` 生命周期
- **AND** 未装配 `ToolFailureMiddleware` 的路径与整轮 SSE `error` SHALL NOT 适用本规格
