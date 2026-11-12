# Delta: agent-delivery — typed 主路径适用范围收窄

## MODIFIED Requirements

### Requirement: Runtime raw event SHALL 只经过一条 typed 主路径

`COMMON_QA` 与 `SUPER_AGENT_QA` 的 raw event SHALL 由唯一 RuntimeEventMapper 映射为 typed RunEvent，再交给 RunHandle 和 Delivery。SSE 字符串 SHALL 只在 SseDelivery 边界编码；主路径 SHALL NOT 保留内部 SSE encode/parse 往返、Web 专用重复 EventBus 或 active registry。

#### Scenario: 废弃类型不接受新 run

- **WHEN** raw event 流向 typed 主路径
- **THEN** 仅 `COMMON_QA` 与 `SUPER_AGENT_QA` 的 run 在主路径上被验收
- **AND** `FAULT_OPERATION_QA` 不存在新 run（见 `agent-profiles`）
