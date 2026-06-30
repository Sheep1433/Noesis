## MODIFIED Requirements

### Requirement: qa_type 路由

系统 SHALL 在 `qa_service`（及等价问答入口）根据请求体 `qa_type` 路由至已注册 Agent 流水线，并在会话 `extra` 中记录该类型。

| `qa_type` | Agent / 协调器 | 详细 spec |
|-----------|----------------|-----------|
| `COMMON_QA` | `GeneralQAAgent` | `agent-common-qa` |
| `FAULT_OPERATION_QA` | `FaultOperationAgent` | `agent-fault-operation` |
| `TEST_CASE_QA` | `CaseCoordinator` | `agent-test-case` |
| `SUPER_AGENT_QA` | `SuperAgent` | `agent-super-agent` |

未知或未注册的 `qa_type` **SHALL NOT** 静默进入任一 Agent；错误处理遵循项目 API 约定。`DEEP_RESEARCH_QA` **SHALL** 视为未注册（已废弃）。

#### Scenario: 流式请求携带已注册 qa_type

- **WHEN** 客户端对 `POST /api/chat/sessions/stream` 提交合法载荷且 `qa_type` 为上表四者之一
- **THEN** 系统 SHALL 调用对应 Agent 流水线，且会话元数据中记录该 `qa_type`

#### Scenario: 未知 qa_type

- **WHEN** 请求体 `qa_type` 不在已注册枚举内（含 `DEEP_RESEARCH_QA`）
- **THEN** 系统 SHALL 返回错误，**SHALL NOT** 默认落入 `COMMON_QA` 或 `SuperAgent`

## ADDED Requirements

### Requirement: 历史 qa_type 展示映射

前端与会话列表 **MAY** 将数据库中已存的 `extra.qa_type=DEEP_RESEARCH_QA` 只读展示为「智能体」或等价文案；**SHALL NOT** 向流式 API 发送 `DEEP_RESEARCH_QA` 作为新会话类型。

#### Scenario: 历史会话标签

- **WHEN** 会话 `extra.qa_type` 为 `DEEP_RESEARCH_QA` 且用户打开历史记录
- **THEN** UI SHALL 展示友好标签（如「智能体」），**SHALL NOT** 崩溃或空白

### Requirement: 超级智能体 Tab 与欢迎页

前端聊天页 **SHALL** 提供 `SUPER_AGENT_QA` 入口（替换原「深度研究」Tab），默认文案 **SHALL** 为「智能体」或产品确认后的等价名称；欢迎页 gradient **SHALL** 使用 `SUPER_AGENT_QA` 主题键。

#### Scenario: 选择智能体 Tab

- **WHEN** 用户点击智能体 Tab 并发送消息
- **THEN** 请求体 `qa_type` SHALL 为 `SUPER_AGENT_QA`
