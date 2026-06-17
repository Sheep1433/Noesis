## ADDED Requirements

### Requirement: 会话软删除 SHALL 触发 Agent 工作区清理

当用户软删除本人会话时，系统 SHALL 在 `ChatService` 完成会话软删后 **始终** 调用 `delete_session_workspace(user_id, session_id)`，删除 `.data/agent_workspace/users/{user_id}/sessions/{session_id}/` 整棵子树。

**SHALL NOT** 改变 SSE、消息软删或 `.data/chat_attachments/` 附件 TTL 语义；**SHALL NOT** 新增 REST 路径或配置开关以跳过磁盘清理。

#### Scenario: 删会话后工作区不可访问

- **WHEN** 用户删除会话 `s1`
- **THEN** `.data/agent_workspace/users/{user_id}/sessions/s1/` SHALL 被移除

#### Scenario: 删他人会话不清理磁盘

- **WHEN** 用户 A 尝试删除用户 B 的会话
- **THEN** SHALL 返回 404 或等价未授权语义，**SHALL NOT** 删除用户 B 的工作区
