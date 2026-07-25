## ADDED Requirements

### Requirement: 工具失败类型归属 harness

工具失败分类与异常类型的权威实现 SHALL 位于 `noesis.errors.tool_failure`。平台 SSE/Delivery 映射层 SHALL 直接使用该路径，**SHALL NOT** re-export 或维护第二份互斥分类表。

#### Scenario: 导入路径

- **WHEN** middleware 或 web tools 抛出基础设施失败
- **THEN** SHALL 使用 `noesis.errors.tool_failure.ToolInfrastructureError`
