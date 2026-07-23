## ADDED Requirements

### Requirement: HITL SSE 事件 hitl-required

当 SuperAgent（或未来启用 HITL 的 Agent）流式执行中 LangGraph 产生 human-in-the-loop interrupt 时，SSE 流 **SHALL** 在工具实际执行之前发出事件 `hitl-required`。

事件 `data` JSON **SHALL** 包含：

| 字段 | 说明 |
|------|------|
| `type` | 固定 `"hitl-required"` |
| `interrupt_id` | 本次 interrupt 的稳定标识，resume 时回传 |
| `session_id` | 当前会话 |
| `message_id` | 当前 assistant 消息 id |
| `kind` | `"approval"` 或 `"clarification"`；当且仅当全部 `action_requests[].name` 为 `ask_user` 时为 `clarification` |
| `action_requests` | 对齐 LangChain `HITLRequest.action_requests`（含 `name`、`args`、`description`） |
| `review_configs` | 对齐 `review_configs`（含 `allowed_decisions`） |
| `expires_at` | Unix 秒，超时时刻 |

发出 `hitl-required` 后，本段 HTTP SSE **SHALL** 再发出 `finish`（`finish_reason=hitl_pending`）与 `data: [DONE]`，结束该次 StreamingResponse。该收尾 **SHALL NOT** 将 assistant 落库为 `completed`；assistant **SHALL** 保持 `status=streaming`，`content.parts` 记录 pending HITL，供后续 resume 续写同一 `message_id`。

#### Scenario: 审批类 interrupt 的 SSE

- **WHEN** 模型调用需审批的 `execute` 且 HITL interrupt 触发
- **THEN** SSE **SHALL** 包含 `hitl-required` 且 `kind` 为 `approval`，随后 **SHALL** 以 `finish_reason=hitl_pending` 与 `[DONE]` 结束本段流

#### Scenario: hitl_pending 不完成落库

- **WHEN** 客户端已收到 `hitl-required` 且本段流以 `hitl_pending` 结束
- **THEN** 服务端 **SHALL NOT** 将 assistant 标记为 `completed`，**SHALL** 保持可 resume 的 pending 状态

### Requirement: HITL resume API

系统 **SHALL** 提供：

```
POST /api/chat/sessions/{session_id}/hitl/resume
```

请求体 **SHALL** 包含：

- `interrupt_id`（string，必填）
- `decisions`（数组，必填）：每项为 `{ "type": "approve" | "reject" | "respond", "message"?: string }`，与 LangChain `HITLResponse` 对齐
- `grant_scope`（可选）：`once` | `session`，仅对网络类 `execute` 审批有效

该接口 **SHALL** 要求已认证且 CSRF 校验（与 `POST .../stop` 同级）；**SHALL** 校验 `session_id` 归属当前用户。

成功时响应 **SHALL** 为新的 `text/event-stream`（与 `POST .../test-case/resume` 同形），输出后续 SSE 事件（含 `tool-output-available`、文本增量、再次 `hitl-required` 或最终 `finish`/`[DONE]`），并续写同一 `assistant_message_id`。

#### Scenario: 合法 resume 继续流

- **WHEN** 已认证用户对本人会话提交合法 `interrupt_id` 与 `decisions`
- **THEN** 系统 **SHALL** 返回 SSE 流，且 Agent 图 **SHALL** 从 interrupt 点继续执行

#### Scenario: 跨用户 resume 拒绝

- **WHEN** 用户 A 对用户 B 的 `session_id` 调用 hitl/resume
- **THEN** 系统 **SHALL** 返回 404 或等价无权限结果，**SHALL NOT** 恢复图执行

### Requirement: assistant 消息 HITL 部件状态

同一轮 assistant 消息（单行 `message_id`）在 HITL 等待期间（含首段流已 `[DONE]` 之后）**SHALL** 保持 `status=streaming`。`content.parts` 中对应 tool part **MAY** 包含扩展字段记录 `hitl` 状态（`pending` / `approved` / `rejected` / `answered`）。

resume 段流正常结束后 **SHALL** 按既有规则终态落库；**SHALL NOT** 为 HITL 新增第二行 assistant 消息。

#### Scenario: HITL 不破坏单行落库

- **WHEN** 一轮流式回复经历 interrupt 与 resume 后正常结束
- **THEN** 数据库 **SHALL** 仅有一行对应 `message_id` 的 assistant 记录
