# Agent Memory 2026 研究洞察

> 调研日期：2026-08-18

## 结论摘要

当前 Agent Memory 正从“持久化聊天摘要”转向“可治理的状态层”。代表性系统的竞争点不是是否使用 embedding，而是：记忆是否有类型、是否能解释来源、事实变化能否保留历史、检索结果是否经过控制、Agent 是否能从 Run 经验中形成可迁移知识。

## 发现 1：最新产品把记忆和 RAG 分成两种职责

**证据等级：A/B**

Letta 将 memory block、file、archival memory 和 external RAG 按重要性与规模分工；Spacebot 将用户/Agent identity 文件与结构化 memory graph 分开；Hermes 也将内置 `MEMORY.md/USER.md` 与外部 Memory Provider 分开。Supermemory 则把 memory、RAG、profiles、connectors 和 extraction 作为不同 primitive 组合起来。

**判断**：Noesis 不应把 RAG 文档和 Agent 记忆混成一个 collection。记忆的核心对象是用户偏好、项目决策、Run 经验和失败模式；知识库的核心对象是外部文档证据。

## 发现 2：最有辨识度的能力是版本化事实和 belief revision

**证据等级：A/B**

Graphiti 使用 validity window 和 episode provenance；HydraDB宣传 Git-style bitemporal graph；Kumiho以 versioned graph 和 formal belief revision 作为产品定位；Spacebot至少实现 Updates/Contradicts 关系和旧记忆 importance 衰减；Mem0提供显式 update 和 expiration。

**判断**：Noesis 的亮点应从“记住了什么”转到“事实变化后，Agent 如何知道现在什么是真的，以及过去什么曾经是真的”。这与 Noesis 的长任务、技术决策和故障诊断场景直接相关。

## 发现 3：Raw recall 直接注入 Context 可能伤害 Agent

**证据等级：A**

Hindsight 的 2026-07-27 coding-agent 设计规格记录了三种对照：Reflect-injection、Recall-per-prompt 和 no-memory。其测试中，逐轮注入未经整理的 recall 结果的版本低于 no-memory，设计因此改为 session-start Reflect、缓存知识页和按需 section 注入。

**判断**：Noesis 不能把 `search_memory` 的 top-k 原始结果直接拼入 system prompt。需要把 `recall` 与 `reflect` 分开：recall 返回证据，reflect 生成短的、带来源的 context bulletin；高风险或需要核对时再打开原始来源。

## 发现 4：Cortex / Bulletin 是适合 Noesis 的高级亮点

Spacebot 的 Cortex 负责跨 channel、worker、branch 的 working memory；Hindsight 将 observations 和 mental models 作为高层记忆；Hermes 的结构化记忆提案也包含周期性 Memory Bulletin。

**判断**：Noesis 可以做一个“Run-aware Memory Cortex”，每次 Agent Run 前输出有限的：

- 用户偏好；
- 当前项目状态；
- 近期相关决策；
- 相关历史 Run 的成功/失败经验；
- 需要注意的旧事实或冲突。

这比普通的 `search_memory` 更能体现 Runtime 设计能力。

## 发现 5：记忆写入正在从“每轮追加”转向“事件后异步巩固”

Hermes provider 有 `sync_turn` 和 `prefetch` 生命周期；Mem0 的平台接口异步排队；Spacebot 在对话空闲后后台保存 memory/skills；Hindsight retain 后后台做 observation consolidation。

**判断**：Noesis 应把实时路径限制为显式 `remember`、`forget` 和高风险纠正。普通对话、工具轨迹和 Run 结果进入异步 Reflector。这样既保护 SSE 延迟，也能把完整 Run 轨迹和失败原因纳入记忆。

## 发现 6：Noesis 不需要一开始引入独立图数据库

Spacebot 使用 SQLite + LanceDB，Hindsight 使用 PostgreSQL/pgvector 形态，Graphiti 才是专门的 temporal graph engine。它们说明图关系重要，但不等于必须部署 Neo4j。

**判断**：Noesis 可使用现有 PostgreSQL 保存 typed memory、版本和少量关系表，Qdrant 作为派生向量索引。第一阶段只实现四种关系：`updates`、`contradicts`、`caused_by`、`result_of`。关系语义比图数据库品牌更重要。

## 发现 7：公开评测必须拆成“检索质量、记忆更新、Agent 结果”三层

LongMemEval 测长期聊天记忆；MemoryAgentBench 测冲突更新和长期理解；PrecisionMemBench 只测返回集合精度；AMA-Bench 测长 Agent trajectory 的 memory construction/retrieval；RECON 测级联失效和多跳关系推理。

**判断**：Noesis 不能只报一个端到端 QA 分数。应至少同时报告：

- recall precision / recall；
- stale fact 和 supersession 错误率；
- evidence coverage；
- context token overhead；
- 工具失败复现或 Run 成功率。

## Noesis 推荐定位

> Run-aware Memory Cortex：面向长任务 Agent 的经验记忆与上下文编排层。

它不是个人偏好记忆，也不是 RAG wrapper。它保存 Agent 运行中的决策、工具失败、成功经验、产物和用户纠正，并在下一次相关 Run 前生成经过 Reflect 的短上下文。
