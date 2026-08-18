# Noesis Agent Memory 2026 研究报告

> 版本：v1.0
> 生成时间：2026-08-18
> 研究深度：deep
> 来源数量：32（学术 15 / 行业 17）
> 状态：Research；不代表当前 Noesis 已实现

## 执行摘要

2026 年的 Agent Memory 竞争点已经从“能否跨会话检索”转向“能否维护 Agent 的可更新状态”。Hermes 当前仍以 `MEMORY.md/USER.md` 为内置记忆，并提供可插拔外部 provider；Spacebot、Hindsight、Graphiti/Zep、HydraDB、Kumiho 和 Supermemory 等更先进的产品则共同强调 typed memory、时间/版本、provenance、混合检索和后台 consolidation。Hindsight 的最新 coding-agent 设计还显示，逐轮注入 raw recall 可能产生负收益，Reflect 生成的知识页和受预算控制的 section 更适合进入 Context。

Noesis 不应继续做普通的文件记忆或 RAG wrapper。建议建设 `Run-aware Memory Cortex`：以 PostgreSQL 保存 typed memory、时间版本、关系和 Run provenance，以 Qdrant 作为派生语义索引；通过 Run 后异步 Reflect、Context Compaction 提取和 session-start Bulletin，让 Agent 能利用工具失败、决策依据和成功经验。图关系只在现有 PostgreSQL 中实现，不先引入独立图数据库。

## 关键结论

1. 文件可以继续保存人工 Identity/Policy，但机器学习到的 Agent 经验应进入结构化状态层。
2. 记忆亮点应是 temporal belief revision、provenance 和 Reflect/Bulletin，而不是单纯 embedding。
3. Noesis 最有差异化的记忆对象是 Run experience：工具失败、决策、产物、验证结果和后续修正。
4. 公共评测用 LongMemEval、MemoryAgentBench、PrecisionMemBench；Noesis 另建 RunMemory 评测集。

## 推荐方案

### 1. 数据模型

最小模型只需要四类节点：

- `identity`：用户、Agent 和项目稳定事实；
- `decision`：技术选择、原因、替代方案；
- `experience`：Run 结果、工具失败、修复方法和产物；
- `observation`：从多个 Run 归纳出的稳定模式。

关系只实现：

- `updates`；
- `contradicts`；
- `caused_by`；
- `result_of`。

每个节点带 `valid_from`、`valid_to`、`recorded_at`、`importance`、`confidence`、`source_run_id`、`source_message_id` 和 `status`。

### 2. 写入流程

```text
completed Run
  → extract candidate memories
  → attach tool / artifact / message evidence
  → typed normalization
  → revision and conflict check
  → PostgreSQL commit
  → Qdrant derived index update
```

实时路径只处理用户明确的 `remember`、`forget` 和纠正。普通聊天和工具轨迹进入后台 Reflector。Context Compaction 触发前先抽取 decision/experience，避免压缩时丢失关键工程经验。

### 3. 读取流程

```text
query
  → lexical + vector candidate retrieval
  → validity / scope / relation filtering
  → Reflect or Bulletin synthesis
  → token budget trim
  → context injection with evidence IDs
```

不要把 top-k 原始搜索结果直接注入 system prompt。`recall` 负责找证据，`reflect` 负责解释和综合，`get_source` 负责必要时回到原始 Run。

### 4. Noesis 的特有亮点

- **Run-aware memory**：记忆绑定 `run_id`、工具调用、产物和验证结果；
- **Temporal revision**：技术决策改变时保留旧决策和生效时间，不静默覆盖；
- **Failure-to-observation**：相同 MCP/安装错误在多个 Run 出现后，沉淀为观察记忆；
- **Memory Cortex**：把 Identity、当前项目、相关决策和历史经验生成短 Bulletin；
- **Skill promotion gate**：只有重复成功且经过验证的经验，才生成 Skill candidate，不自动修改 Skill；
- **Traceable retrieval**：每条 Bulletin 结论都能回到 memory node 和原始消息。

## 不采用的方案

- 不把 `USER.md/AGENTS.md`、PostgreSQL、Qdrant 各自当成独立事实源；
- 不一开始实现 8 种以上 memory type；
- 不一开始引入 Neo4j/Graphiti；
- 不让每轮对话同步调用 LLM 做记忆整理；
- 不把所有工具原始输出向量化；
- 不把“自动调整 Prompt”或模型微调写成记忆能力；
- 不只报一个 LongMemEval 总分。

## 评测组合

### P0

- LongMemEval：跨会话、时间、知识更新、abstention；
- PrecisionMemBench：scope、supersession、空结果和返回集合精度；
- Noesis RunMemory-50：工具失败、决策变更、Run provenance、compaction recovery。

### P1

- MemoryAgentBench Conflict Resolution / Accurate Retrieval；
- LoCoMo evidence-based QA；
- MemBench capacity / efficiency。

### P2

- AMA-Bench：长 Agent trajectory 和 state updating；
- RECON：级联失效、来源冲突、反事实和时序约束；
- LongMemEval-V2：未来接入 web-agent 或多模态 trajectory 后再使用。

## 简历可写成果

实现后可以写：

> 设计并实现面向长任务 Agent 的 Run-aware Memory Cortex：基于 PostgreSQL 构建 typed、temporal、evidence-backed memory state，以 Qdrant 提供派生语义检索；通过 Run 后异步 Reflect、Context Compaction 提取、冲突修订和 token-budgeted Memory Bulletin，使 Agent 能利用工具失败、技术决策和验证结果，并支持从每条记忆回溯到原始 Run 证据。

第二条：

> 构建 Memory Eval Contract，使用 LongMemEval、MemoryAgentBench Conflict Resolution、PrecisionMemBench 和 50 条 Noesis RunMemory 场景，分别评估跨会话召回、过期事实抑制、检索精度、证据覆盖率、Context Token 开销与历史经验对任务成功率的影响。

## 局限性

- Hermes 的结构化记忆 issue 是设计记录，不代表主分支已经完成；
- HydraDB、Kumiho、Supermemory 的性能和 SOTA 数据主要来自产品方，不能直接作为独立事实；
- LongMemEval、LoCoMo 等 benchmark 的 answer model、judge 和记忆输入协议不统一，分数不可直接横向比较；
- Noesis 还需要用真实 Run 轨迹验证“经验记忆是否提升工具任务成功率”，不能仅凭 QA benchmark 下结论。

## 参考文献

详细来源见同目录 `sources/filtered-sources.json`。核心链接：

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [Spacebot Memory](https://docs.spacebot.sh/memory)
- [Hindsight](https://github.com/vectorize-io/hindsight)
- [Graphiti](https://github.com/getzep/graphiti)
- [HydraDB](https://hydradb.com/)
- [Kumiho](https://kumiho.io/en)
- [LongMemEval](https://arxiv.org/abs/2410.10813)
- [MemoryAgentBench](https://arxiv.org/abs/2507.05257)
- [PrecisionMemBench](https://tenureai.dev/benchmark/)
- [RECON](https://arxiv.org/abs/2607.16716)

## 质量门禁

- [x] 核心结论至少由两个独立来源支持；
- [x] 产品当前能力与提案已区分；
- [x] 产品方 benchmark 已标记为 vendor claim；
- [x] 评测集按能力而非只按知名度选择；
- [x] Noesis 建议与当前实现已区分。
