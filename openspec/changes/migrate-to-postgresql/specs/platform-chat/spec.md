## ADDED Requirements

### Requirement: 聊天关系数据 SHALL 在 PostgreSQL 中保持既有持久化语义

`/api/chat` 相关接口和流式问答 SHALL 将会话、消息与附件元数据持久化到 PostgreSQL。数据库后端切换 SHALL NOT 改变既有 API 响应、SSE 事件类型、assistant 骨架—检查点—终态单行落库语义、用户隔离或软删除行为。

#### Scenario: 流式 assistant 在 PostgreSQL 单次终态落库

- **WHEN** 客户端通过 `POST /api/chat/sessions/stream` 完成、停止或中断一次流式问答
- **THEN** 系统 SHALL 在 PostgreSQL 中按既有状态机将对应 assistant 消息持久化为同一行的 completed、partial 或 error 终态，且 SSE 对外帧保持兼容

#### Scenario: PostgreSQL 会话可读取

- **WHEN** 用户访问其 PostgreSQL 中的聊天会话或消息
- **THEN** `/api/chat/sessions` 及消息查询接口 SHALL 返回正确的归属可见性、内容和状态，而不得暴露其他用户数据
