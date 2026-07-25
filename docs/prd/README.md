# PRD 索引

PRD 解释当前产品与架构。验收条件以对应 OpenSpec 主规格为准。

| 领域 | PRD | OpenSpec |
|------|-----|----------|
| Chat / SSE | [SSE 流式数据设计](platform/SSE流式数据设计.md) | `platform-chat`、`agent-run-delivery` |
| 会话与消息 | [聊天记录设计](platform/聊天记录设计.md) | `platform-chat`、`user-platform` |
| 知识库 | [知识库 RAG 底座](knowledge-base/知识库RAG底座详细设计.md) | `knowledge-base` |
| 测试用例生成 | [测试用例生成设计](agent-test-case/测试用例生成设计.md) | `agent-profiles`、`offline-evals` |
| 故障运维 | [故障运维 Agent 设计](agent-fault-operation/故障运维设计.md) | `agent-profiles`、`agent-hitl` |

尚未实现的设计只写在 `openspec/changes/<change>/`。PRD 不提前描述未批准方案。
