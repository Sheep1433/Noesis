## ADDED Requirements

### Requirement: TEST_CASE_QA 与 test-case 恢复流 SHALL 发出阶段进度 SSE 事件

在 `POST /api/chat/sessions/stream` 且 `qa_type` 为 `TEST_CASE_QA`，以及 `POST /api/chat/sessions/{session_id}/test-case/resume` 的流式响应中，系统 SHALL 除既有文本与业务事件外，发出 **`phase-start`、`phase-delta`、`phase-end`** 三类 SSE `data:` JSON 事件（`type` 字段取值与帧语义一致），用于表示测试用例生成流水线的阶段进度。**其它 `qa_type` 的路径 SHALL NOT** 因本要求而必须发送 `phase-*` 事件。

每一段阶段进度 SHALL 使用稳定机器标识 **`phaseId`**（`snake_case` 字符串）；系统 SHALL 为至少以下阶段在全流程中可提供展示用 **`title`** 或等价人类可读字段（可与 `phaseId` 不同，且不替代 `phaseId`）：`parse_requirements`（解析需求与附件上下文）、`generate_test_points`（生成场景与测试点）、`await_user_confirm`（待用户勾选确认）、`parallel_generate_cases`（并行生成用例）。

同一逻辑阶段 SHALL 在同一用户回合内以 **`phase-start` → （零次或多次）`phase-delta` → `phase-end`** 的顺序出现；**同一 `phaseId` 的 `phase-start` 与该阶段的 `phase-end` SHALL 成对出现**。

`phase-start` 的 `data:` JSON SHALL 包含至少：`type`、`phaseId`。`phase-end` SHALL 包含至少：`type`、`phaseId`。`phase-delta` SHALL 包含至少：`type`、`phaseId`，并 SHOULD 承载阶段内可读增量，推荐使用与现有文本增量对齐的载荷键（例如 `textDelta`；具体键名以实现与 `useSSEStream` 消费者一致为准，并在契约测试中固定）。

系统在阶段因用户停止、错误或业务中止而结束时，SHALL 仍发送对应 `phase-end`（与同一 `phaseId` 配对），并在载荷中给出 **`ok` 布尔字段或项目约定的等价成功标记**，以便前端区分正常结束与中断；顶层 **`error`** 帧 behavior 以既有「流式问答与 SSE 契约」为准，本要求不顶替全局错误语义。

前端对未识别的 SSE `type` 或未知可选键 SHALL **继续兼容忽略**，不得因新增 `phase-*` 而导致解析失败。

#### Scenario: 首次发起 TEST_CASE_QA 流时出现阶段序列

- **WHEN** 客户端对 `POST /api/chat/sessions/stream` 提交合法载荷且 `qa_type` 为 `TEST_CASE_QA`
- **THEN** 系统在业务执行过程中 SHALL 至少在进入「解析需求」「生成测试点」「等待确认」「并行生成用例」等对应节点前后，依次发出与各 `phaseId` 一致的 `phase-start` 与 `phase-end`，且帧为合法 SSE 包裹的 JSON

#### Scenario: resume 后继续阶段进度

- **WHEN** 客户端在用户确认测试点后调用 `POST /api/chat/sessions/{session_id}/test-case/resume` 并成功建立流
- **THEN** 系统 SHALL 为恢复后的执行段落继续发出与新段落语义一致的 `phase-*` 事件（包含 `parallel_generate_cases` 或与实现一致的等价阶段标识），且不丢失与同一会话中既有 `phaseId` 配对规则的一致性

#### Scenario: 非测试用例流不强制 phase 事件

- **WHEN** 客户端对 `POST /api/chat/sessions/stream` 提交合法载荷且 `qa_type` 不为 `TEST_CASE_QA`
- **THEN** 系统 SHALL NOT 因满足本 Requirement 而被要求在同一连接上发射 `phase-start`/`phase-delta`/`phase-end`

## MODIFIED Requirements

### Requirement: SSE 对外契约 SHALL 具备自动化回归覆盖

系统 SHALL 为 `LangGraphSseBridge`（或统一的 SSE 字符串格式化入口）提供自动化测试，覆盖至少：`message-start` 形态、`text-delta` 含 `textDelta`、`finish` 含 `finishReason`/`usage`、`data: [DONE]` 收尾；断言方式为解析 `data:` 行 JSON 键集合或关键字段类型，防止静默破坏 `useSSEStream` 消费者。**对测试用例阶段事件，SHALL** 在以 `CaseCoordinator`（或等价）合成事件为输入的路径上，增加对 `phase-start`、`phase-end`（及对 `phase-delta` 若实现）帧的 **`type`/`phaseId` 与必选键**断言，帧边界规则与既有 golden 断言一致。

#### Scenario: 桥接层最小 golden 断言

- **WHEN** 测试向桥接传入代表「消息开始」与「文本增量」的合成事件并收集输出字符串
- **THEN** 输出 SHALL 包含合法 SSE 帧边界且 `data:` 负载可被 `json.loads` 解析，且 `type` 字段与事件名一致

#### Scenario: 测试用例 phase 帧 golden 断言

- **WHEN** 测试向与实际流式编码一致的入口传入至少一组 `phase-start` 与同 `phaseId` 的 `phase-end` 合成载荷（及对 `phase-delta` 若存在）
- **THEN** 收集到的输出字符串 SHALL 满足合法 SSE 帧边界，每一段业务 `data:` SHALL 可被 `json.loads` 解析，且 SHALL 包含与约定一致的 `type` 与 `phaseId`，`phase-end` SHALL 包含 `ok` 或项目约定的等价成功标记字段
