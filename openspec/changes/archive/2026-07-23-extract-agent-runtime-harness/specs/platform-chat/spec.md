## MODIFIED Requirements

### Requirement: 问答编排经 AgentRunService 委托 Runtime

`QaService.exec_query` 对 `COMMON_QA`、`SUPER_AGENT_QA`、`FAULT_OPERATION_QA` **SHALL** 经 `AgentRunService.start_run` 启动 Agent 执行，并将流式事件交由既有 `LangGraphSseBridge` 转换为 SSE。`QaService` **SHALL** 继续负责 assistant 消息骨架—检查点—终态落库、stop token 与用户中断语义；**SHALL NOT** 在 service 内直接维护 `GeneralQAAgent` / `SuperAgent` / `FaultOperationAgent` 的 `astream_events` 循环。对外 `POST /api/chat/sessions/stream` 路径与 SSE 事件类型 **SHALL** 保持兼容。

#### Scenario: COMMON_QA 流式问答

- **WHEN** 客户端以 `qa_type=COMMON_QA` 发起流式问答
- **THEN** `QaService` **SHALL** 调用 `prepare_runtime_context` 填充 Context
- **AND** **SHALL** 调用 `AgentRunService.start_run`
- **AND** 客户端 **SHALL** 仍收到既有 `reasoning-*`、`text-*`、`tool-call-start`、`tool-output-available`、`finish`、`[DONE]` 事件序列

#### Scenario: 用户停止不影响落库契约

- **WHEN** 用户在流式过程中触发 stop
- **THEN** `AgentRunService.cancel_run` **SHALL** 中断 Runtime 执行
- **AND** `QaService` **SHALL** 仍将 assistant 消息更新为 `partial` + `finish_reason=stopped`，行为与迁移前一致

#### Scenario: TEST_CASE_QA 可暂不迁移

- **WHEN** `qa_type=TEST_CASE_QA`
- **THEN** **MAY** 继续使用 `CaseCoordinator` 直至 StateGraph 适配完成
- **AND** **SHALL NOT** 因本 Requirement 破坏测试用例 SSE 阶段语义
