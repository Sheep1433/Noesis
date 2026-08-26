# Noesis Agent Memory 2026 调研计划

> 状态：Research
> 调研日期：2026-08-18
> 研究目标：为 Noesis 选择一个能体现 Agent Runtime 能力、又能在个人项目中落地和评测的记忆模块。

## 1. 核心研究问题

1. 2026 年代表性 Agent 产品的记忆模块，事实源、索引、图结构和上下文注入如何分工？
2. Hermes、Spacebot、Hindsight、HydraDB、Kumiho、Supermemory、Mem0、Graphiti 等产品的亮点分别是什么？哪些已经实现，哪些只是产品宣传或提案？
3. Noesis 的长任务、MCP、工具失败、SSE、Checkpoint 和多 Agent 背景，最适合突出哪一个记忆问题？
4. 哪些公开评测集能验证跨会话记忆、冲突更新、时序推理、检索精度和 Agent 轨迹经验？
5. 如何把设计压缩成有限的核心能力，避免成为数据库、知识图谱和 RAG 的堆叠？

## 2. 检索关键词矩阵

| 方向 | 关键词 |
|---|---|
| 产品 | Hermes Agent memory provider, Spacebot memory cortex, Hindsight retain recall reflect, HydraDB temporal graph, Kumiho belief revision, Supermemory graph memory |
| 记忆机制 | typed memory, temporal memory, belief revision, memory consolidation, reflect injection, context bulletin, compaction memory extraction |
| 检索 | hybrid retrieval, vector FTS graph RRF, retrieval precision, adaptive return sizing |
| 评测 | LoCoMo, LongMemEval, MemoryAgentBench, MemBench, AMA-Bench, BEAM, RECON, PrecisionMemBench |
| Noesis | LangGraph agent memory, MCP tool memory, run experience memory, agent failure memory |

## 3. 数据源与筛选标准

- 优先官方文档、官方源码、论文原文和官方数据集仓库。
- 产品官网的 benchmark、SOTA 和用户量只作为产品方宣称，不作为独立事实。
- GitHub issue / design spec 标记为“提案或设计记录”，不当作已发布能力。
- 评测集优先选择有公开数据、代码或明确任务协议者。
- 研究时间范围：2023-2026；经典架构论文可放宽。

## 4. 预期产出

- 产品架构横向比较；
- Noesis 推荐的 Run-aware Memory Cortex 方案；
- P0/P1/P2 实施边界；
- 公共评测集和 Noesis 自建评测集组合；
- 风险、未验证问题和不可写入简历的内容。
