## ADDED Requirements

### Requirement: 平台 SHALL 提供会话上下文浏览 API

系统 SHALL 在 `chat` 路由下提供 `GET /api/chat/sessions/{session_id}/context` 与 `GET /api/chat/sessions/{session_id}/workspace/file`，行为遵循 `chat-session-context-panel` 规格。

#### Scenario: context API 注册

- **WHEN** 应用启动并加载 `chat_api` 路由
- **THEN** OpenAPI **SHALL** 包含上述两个 GET 端点

### Requirement: 删会话 SHALL 清理统一用户会话子树

用户软删或批量删除本人会话时，`ChatService` SHALL 调用 `user_data_paths.delete_session_data(user_id, session_id)`，删除 `.data/users/{user_id}/sessions/{session_id}/`（含 workspace 与附件目录）。

本行为 **SHALL** 取代仅调用 `delete_session_workspace` 清理 `.data/agent_workspace/` 的旧逻辑。

#### Scenario: 单会话删除清磁盘

- **WHEN** 用户 `DELETE /api/chat/sessions/{session_id}` 成功
- **THEN** `.data/users/{uid}/sessions/{session_id}/` SHALL 不再存在于磁盘

#### Scenario: 批量删除

- **WHEN** 用户 `POST /api/chat/sessions/batch-delete` 成功删除多个会话
- **THEN** 每个对应 `sessions/{session_id}/` 子树 SHALL 被删除
