## Purpose

本增量规定在启用 Langfuse 追踪时，流式聊天域如何将**会话级引用**与现有 SSE 契约对齐：所有增量均为可选，关闭开关时与现网 JSON 结构一致。

## ADDED Requirements

### Requirement: 流式问答与 Langfuse 会话关联（可选）

当 `LANGFUSE_TRACING_ENABLED` 为真时，系统 SHALL 在结构化日志中记录与当次流式请求一致的 `langfuse_session_id`（或等价会话键）以便排障。系统 MAY 在 `POST /api/chat/sessions/stream`（及同会话语义下其他流式端点）的 SSE `data:` JSON 中增加**可选**键（例如面向调试的会话/观测引用），前端对未知键 SHALL 必须忽略而不影响解析。

#### Scenario: 关闭 Langfuse 时不增加字段

- **WHEN** `LANGFUSE_TRACING_ENABLED` 不为真
- **THEN** 流式 SSE 帧的 JSON SHALL 不因本能力增加新的必选或推荐键

#### Scenario: 启用时可选键不破坏协议

- **WHEN** 启用且实现选择向 SSE 透出引用
- **THEN** 该键 SHALL 为可选，且 SHALL NOT 包含 `LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY` 或 JWT 等密钥类内容
