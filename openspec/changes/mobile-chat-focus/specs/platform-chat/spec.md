## ADDED Requirements

### Requirement: 聊天页模式文案 SHALL 保持 qa_type 路由兼容

网页聊天入口 SHALL 以“聊天”“任务”“故障排查”呈现 `COMMON_QA`、`SUPER_AGENT_QA`、`FAULT_OPERATION_QA`，但发送请求、URL 深链和历史会话仍 SHALL 使用既有 `qa_type` 常量。`POST /api/chat/sessions/stream` 与 SSE 契约 SHALL 保持兼容。

#### Scenario: 从任务模式发送

- **WHEN** 用户在“任务”模式发送消息
- **THEN** 请求 SHALL 使用 `qa_type=SUPER_AGENT_QA`
- **AND** SSE 事件与 assistant 落库行为 SHALL 与变更前一致

#### Scenario: 通过深链进入故障排查

- **WHEN** 用户打开带有 `qa_type=FAULT_OPERATION_QA` 的合法聊天深链
- **THEN** 页面 SHALL 恢复该内部类型并显示“故障排查”

