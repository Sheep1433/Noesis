# 工程文档

Noesis 的系统组成与工程专题都在这里：当前长期有效的边界、组件职责、数据流与约束（原 architecture 目录，2026-09-01 并入），以及有明显难度的实现——问题背景、方案选择、失败场景、操作手册与可借鉴经验。

不重复 OpenSpec 的完整 Scenario，也不提前把研究建议写成当前事实；尚未落地的方案留在 OpenSpec change 或 `docs/research/`。普通功能说明不放在这里。

## 平台（platform/）

- [Agent Memory](platform/agent-memory.md)：md 文件记忆层——五类条目、水位增量抽取、AutoDream 门控整理、每 Run 注入选条。
- [Chat Streaming](platform/chat-streaming.md)：SSE 流式数据架构、多 Tab 恢复、信令流、事件词表（§4.2b，契约测试钉住）、失败处理与部署约束。
- [Durable Agent Runs](platform/durable-agent-runs.md)：RunHandle 单写入模型、状态机、API 与多 Tab、恢复与容量。
- [Chat Persistence](platform/chat-persistence.md)：PostgreSQL / Qdrant / LangGraph checkpoint 数据职责与骨架—检查点—终态落库。
- [Messaging Channels](platform/messaging-channels.md)：Telegram / 飞书通道投递模型。
- [Settings Control Plane](platform/settings-control-plane.md)：设置页统一管理、敏感值加密与迁移。
- [Unified Commands](platform/unified-commands.md)：跨端斜杠命令层（单一 registry）。

## 子系统与专题

- [Knowledge Base](knowledge-base.md)：知识库解析、检索与引用链路。
- [Subagent Sessions](subagent-sessions.md)：子 Agent 会话模型（前台子 Agent 与后台任务）。
- [Agent Context Runtime 重构设计](agents/agent-runtime-design.md)（proposed）：Claude Code 式上下文策略、DeepAgents 风格目录、middleware 边界、compaction 与迁移验证。
- [Agent Evaluation](agents/agent-evaluation.md)：评测方法与 harness。
- [Reliable SSE 发布 Runbook](reliable-sse-release-runbook.md)：BREAKING 发布的操作步骤与回滚。
- [旧工作日志](legacy-worklog.md)：原 NOTES.md 未带决策日期的工程操作记录（历史素材）。
