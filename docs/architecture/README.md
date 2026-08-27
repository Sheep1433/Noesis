# 架构设计

这里解释 Noesis 当前长期有效的系统边界、组件职责、调用与数据流、状态、权限和失败处理。

架构文档不重复 OpenSpec 的完整 Scenario，也不提前把研究建议写成当前事实。尚未落地的方案留在 OpenSpec change 或 `docs/research/`。

## 平台（platform/）

- [Agent Memory](platform/agent-memory.md)：md 文件记忆层——五类条目、水位增量抽取、AutoDream 门控整理、每 Run 注入选条。
- [Chat Streaming](platform/chat-streaming.md)：SSE 流式数据架构、多 Tab 恢复、信令流、失败处理与部署约束。
- [Durable Agent Runs](platform/durable-agent-runs.md)：RunHandle 单写入模型、状态机、API 与多 Tab、恢复与容量。
- [Chat Persistence](platform/chat-persistence.md)：PostgreSQL / Qdrant / LangGraph checkpoint 数据职责与骨架—检查点—终态落库。
- [Messaging Channels](platform/messaging-channels.md)：Telegram / 飞书通道投递模型。
- [Settings Control Plane](platform/settings-control-plane.md)：设置页统一管理、敏感值加密与迁移。
- [Unified Commands](platform/unified-commands.md)：跨端斜杠命令层（单一 registry）。

## 其它

- [Knowledge Base](knowledge-base.md)：知识库解析、检索与引用链路。
- [Subagent Sessions](subagent-sessions.md)：子 Agent 会话模型（前台子 Agent 与后台任务）。
