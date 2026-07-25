## ADDED Requirements

### Requirement: 平台聊天 SHALL 持久化 SourcesPart

assistant `content.parts` SHALL 支持 versioned `sources` part，并在 completed、partial、断连终态及 HITL pending/resume 中保持可反序列化、可去重和可回放。旧消息不含 sources part 时 SHALL 按无来源处理。

#### Scenario: partial 历史回放

- **WHEN** 用户停止后重新加载 partial assistant 消息
- **THEN** 客户端 SHALL 从 sources part 恢复与停止前一致的来源状态

### Requirement: 来源 SSE SHALL 经统一 RunEvent Delivery

`/api/chat` 流 SHALL 支持 snake_case 的 `sources-available` 与 `sources-finalized` 事件。`sources-available` SHALL 在对应知识库 tool output 后投递新增来源；`sources-finalized` SHALL 在正文完成后、`finish` 前投递 cited ids 与展示顺序。未知事件对旧客户端 SHALL 为可忽略的向后兼容扩展。

#### Scenario: 正常完成事件顺序

- **WHEN** 一轮 COMMON_QA 检索并生成带有效 source token 的正文
- **THEN** 事件顺序 SHALL 为 tool output、sources available、正文结束、sources finalized、finish

#### Scenario: 客户端断连

- **WHEN** 客户端在 sources finalized 前断开
- **THEN** PersistSink SHALL 继续按 builder 快照持久化来源，SHALL NOT 依赖客户端收到该事件
