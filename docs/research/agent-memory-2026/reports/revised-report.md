# Agent Memory 产品选型修订报告

> 状态：Research / Revised
> 调研日期：2026-08-24
> 关联项目：Noesis
> 依据：`docs/research/agent-memory-2026/` 已有 32 个来源，以及本轮对 OpenClaw、Codex、Hermes、OpenViking、Mem0 官方资料和公开 benchmark 的复核。

## 1. 修订原因

初版建议过早把 Noesis 的差异点放在“Run-aware Memory Cortex”，但没有先回答记忆覆盖率问题。当前实现主要从工具失败→恢复成功的轨迹提取经验，准确性门槛高，但自然失败很少时几乎没有记忆可用。

本报告将“记忆产品哪个好”拆成两件事：

1. 记忆是否能稳定发现值得保留的内容；
2. 记忆是否能在未来任务中安全地产生收益。

存储引擎、向量库和图数据库只是实现手段，不单独代表记忆能力。

## 2. 评分方法

评分是面向 Noesis 长任务 Agent 场景的架构适配分，不是产品 benchmark 分数。权重如下：

| 维度 | 权重 | 判断内容 |
|---|---:|---|
| 内容覆盖 | 25% | 能否从显式请求、Session、compaction、任务结果和失败恢复中产生记忆 |
| 写入质量与安全 | 20% | 去噪、来源可信度、敏感信息和 memory poisoning 防护 |
| 更新与时间语义 | 15% | 冲突、过期、supersession、历史版本 |
| 检索与上下文控制 | 15% | 混合检索、相关性、预算、是否避免 raw recall 污染上下文 |
| 用户治理 | 10% | 查看、确认、修改、失效、删除、可解释来源 |
| Agent Runtime 适配 | 10% | Session、Run、Tool、HITL、multi-agent 和 scope 支持 |
| 工程成本 | 5% | 部署、调试、迁移和维护成本，成本越低分越高 |

产品文档明确的能力与设计提案分开；厂商 benchmark 不作为横向分数依据。

## 3. 横向评分

| 系统 | 覆盖 | 质量/安全 | 更新 | 检索 | 治理 | Runtime | 成本 | 综合 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Noesis 当前实现 | 2 | 8 | 7 | 7 | 8 | 8 | 4 | **5.7** |
| Codex Memories（开源实现） | 9 | 9 | 8.5 | 8 | 9 | 9.5 | 6.5 | **8.6** |
| OpenClaw | 9 | 8.5 | 8 | 8 | 9 | 7.5 | 8 | **8.4** |
| Hindsight | 8.5 | 8.5 | 9 | 9 | 8 | 8 | 5 | **8.3** |
| Mem0 | 8.5 | 7.5 | 7.5 | 9 | 7 | 8 | 8 | **8.1** |
| OpenViking | 9 | 7 | 8 | 9 | 7 | 8 | 5 | **7.9** |
| Graphiti / Zep | 7.5 | 8 | 9.5 | 9 | 7.5 | 7 | 4.5 | **7.9** |
| Letta / MemGPT | 6.5 | 8 | 7 | 8 | 8 | 9 | 7 | **7.7** |
| Hermes Agent | 5 | 8.5 | 6.5 | 6.5 | 9 | 7 | 9 | **7.2** |
| Noesis 目标设计 | 8.5 | 8.5 | 8.5 | 8.5 | 9 | 9 | 7 | **8.6** |

`Noesis 目标设计` 是尚未实现的目标，不应当当作当前能力对外宣称。

`Codex Memories` 的分数是面向 Noesis coding-agent 场景的源码/架构适配分；它不能替代公开 benchmark 的效果成绩。外部 LongMemEval-V2 已经单独报告了 Codex coding-agent memory baseline 的结果，见第 8 节。

## 4. 各系统真正擅长什么

### 4.1 OpenClaw：最完整的产品基线

OpenClaw 当前文档描述的是“短期记录 → 后台整理 → 长期记忆 → 可搜索证据”的完整产品流程。它有 daily notes、`MEMORY.md`、Dream Diary、compaction flush 和后台 Dreaming。Dreaming 通过 recall frequency、query diversity、recency 和 provenance 门控，再由 deep 阶段写入长期记忆；未可信来源在进入 consolidation 前被结构性排除。[OpenClaw memory architecture](https://github.com/openclaw/openclaw/blob/main/docs/concepts/memory-architecture.md) [OpenClaw memory overview](https://docs.openclaw.ai/concepts/memory)

它的优势不是某个向量算法，而是写入时机、可审查结果和长期维护流程已经形成产品体验。缺点是文件、SQLite、Dreaming、插件和不同 memory provider 组合后较复杂，且它的 workspace 假设需要适配 Noesis 的多用户服务端。

**结论：最适合作为 Noesis 的主方案基线。**

### 4.2 Codex Memories：最接近 Noesis 的运行时设计

OpenAI 已将 Codex 的记忆实现开源在 `codex-rs/memories`。它不是简单的 `MEMORY.md` 注入，而是一个异步两阶段流水线：root session 启动时先从 state DB 有界领取近期、已空闲的 rollout；Phase 1 对每个 rollout 提取结构化 `raw_memory` 和 `rollout_summary`，支持并发、租约、失败退避和 `succeeded_no_output`；Phase 2 用全局锁按使用次数与最近使用时间选择候选，整理成文件记忆工作区，再启动一个禁止联网、无审批的 consolidation agent，产出 `memory_summary.md`、`MEMORY.md`、`rollout_summaries/` 和 `skills/`。[Codex Memories README](https://github.com/openai/codex/blob/main/codex-rs/memories/README.md)

它的关键设计有四个：不依赖工具失败才能触发；提取与全局整理分离；只把被使用或新鲜的候选送入整理；读取路径能记录 memory citation 和 usage telemetry。整理 prompt 还明确要求 raw rollout 只作为不可变证据、把工具输出当数据而不是指令、只保存可验证内容并脱敏。[Codex consolidation prompt](https://github.com/openai/codex/blob/main/codex-rs/memories/write/templates/memories/consolidation.md)

这正好覆盖当前 Noesis 的主要缺口：长任务 Run 的稳定覆盖、后台处理、并发幂等、失败重试、记忆使用反馈和可审查文件结果。它的不足是成本偏高、实现仍在快速演进，且官方 issue 已暴露过并发和覆盖边界，不能把它当成已证明的完美方案。

**结论：对 Noesis 这种 coding-agent，Codex 应取代 OpenClaw 成为主流程参考；OpenClaw 的 Dreaming/provenance 规则作为补充。**

#### Codex 的评测证据边界

我重新检查了开源仓库的测试目录、CI 配置和 README：公开部分覆盖的是软件正确性，例如任务领取/租约、watermark、Phase 1/Phase 2 请求、结构化输出、secret redaction、文件同步、symlink 防护、全局锁、citation 解析和 `debug clear-memories`。这些测试能证明“流水线按预期运行”，不能单独证明“原生 Codex Memories 的自动提取真的改善了未来任务”。仓库没有发布原生 memories pipeline 的 gold trajectory、memory-on/off A/B 或单独效果报告。

README 提到的独立 `openai/project/agent_memory/write` harness 并未随公开仓库发布；公开路径目前返回 404。但这不等于 Codex 没有外部效果评测：LongMemEval-V2 通过公开 harness 把 Codex CLI 作为 coding-agent memory controller 跑了完整 baseline。[Codex memories directory](https://github.com/openai/codex/tree/main/codex-rs/memories) [Codex core memory README](https://github.com/openai/codex/blob/main/codex-rs/core/src/memories/README.md)

公开 issue 还显示了必须吸收的反例：有报告称大 rollout 的 Phase 1 会因 context overflow 丢失记忆，甚至出现约 31% 的失败率；另有报告称 stage-1 已成功但 Phase 2 没有生成或注入 summary。这些是用户报告而非统一实验结论，但足以说明“整段 transcript 一次送模型 + 后台静默失败”不能进入 Noesis。[context overflow issue](https://github.com/openai/codex/issues/38860) [stage-1 truncation issue](https://github.com/openai/codex/issues/35093) [missing injection issue](https://github.com/openai/codex/issues/29033)

### 4.3 Hindsight：最适合借鉴读取路径

Hindsight 将 `retain`、`recall`、`reflect` 分开，记忆不仅有事实，还有 experiences、observations 和 mental models。它同时使用 semantic、BM25、graph 和 temporal 信号。更重要的是，它的 coding-agent 设计记录了 raw recall 每轮直接注入可能低于无记忆基线，因此改为 session-start Reflect、缓存知识页和按需 section 注入。

**结论：借鉴它的“召回证据→综合短上下文→必要时展开来源”，不要照搬全部 mental model 和图结构。**

### 4.4 Mem0：最好的工程 API 参考

Mem0 把接入压缩成 `add()` 和 `search()`。`add()` 从消息提取事实、做去重和 embedding；search 支持 user、agent、app、run scope、过滤、阈值和 rerank。[Mem0 how it works](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx) [Mem0 search](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-operations/search.mdx)

它的优势是接入简单、覆盖广。缺点是很多质量判断发生在黑盒 pipeline 里；ADD-only 设计容易积累冗余，复杂冲突和来源治理需要应用层补足。[Mem0 v3 migration](https://docs.mem0.ai/platform/migration/platform-v2-to-v3)

**结论：借鉴 API 形态和 scope 过滤，不采用它的黑盒写入语义。**

### 4.5 OpenViking：最强的上下文数据库方向

OpenViking 在 Session commit 后从对话和 memory policy 提取多种类型的记忆，覆盖 profile、preferences、entities、events、experiences、tools、skills 等，并把资源、记忆和技能统一放进上下文数据库。[OpenViking session extraction](https://github.com/volcengine/OpenViking/blob/main/docs/en/concepts/08-session.md) [OpenViking context types](https://docs.openviking.ai/en/concepts/02-context-types)

它解决的是“一个 Agent 所有上下文如何统一管理”，不是单纯个人记忆。优点是覆盖和检索强，缺点是系统边界大，容易把 RAG、Skill、Memory 都做成一套大型基础设施。Noesis 已经有独立知识库和 Skills，不应整体照搬。

**结论：借鉴 Session commit 提取，不采用统一上下文数据库。**

### 4.6 Graphiti / Zep：时间和事实更新最强

这类方案把 episodes、entities、facts、relationships 和 validity window 作为核心，适合回答“现在是什么、过去是什么、为什么变了”。它们在历史版本、关系推理和时间查询上强，但图存储、实体解析和查询维护成本高。

**结论：只借鉴 temporal validity、updates、contradicts 和 provenance，不先部署独立图数据库。**

### 4.7 Letta / MemGPT：运行时工作记忆最强

Letta 把小型 memory blocks、files、archival memory 和 external RAG 分层，强调 Agent 在运行中通过工具维护自己的核心记忆。它适合有明确 Agent identity 和持续运行状态的场景。

它的弱点是长期自动发现和跨 Session 事实整理不是主要优势。Noesis 可以借鉴它的上下文预算和工作记忆边界，但不能只依赖 Agent 自己决定是否写记忆。

### 4.8 Hermes：简单、透明，但覆盖不够

Hermes 的内置记忆是有硬容量限制的 `MEMORY.md` 和 `USER.md`，会话开始冻结注入，并用 SQLite FTS 搜索历史；记忆主要通过 memory tool 的 add、replace、remove 操作维护。[Hermes memory](https://hermesagent.studio/features/memory)

它适合个人本地 Agent，优点是文件可读、可编辑、成本低。缺点是如果 Agent 不主动调用 memory tool，很多有价值内容不会进入记忆。

**结论：借鉴它的用户可见性、容量约束和人工编辑体验。**

## 5. Noesis 应该采用的单一方案

不是在多个方案中做运行时切换，而是确定一条组合后的主方案：

> Codex 的两阶段 Run 流水线 + OpenClaw 的 provenance/Dreaming 门控 + Hindsight 的读取流程 + Mem0 的简洁接口 + Noesis 的 Run evidence。

具体含义：

### 写入

- 用户明确要求记住：立即处理。
- 每个已结束且满足空闲窗口的 Session/Run：异步提取任务目标、决策、项目进展和可复用经验；不以工具失败为前提。
- Compaction 前：先保存尚未沉淀的重要上下文。
- 工具失败恢复：作为高置信经验来源，不再作为唯一入口。
- 重复纠正和重复成功：经过多次证据后才形成稳定记忆。

提取与整理分成两个后台阶段：第一阶段按 Run 产出带来源的候选；第二阶段按新鲜度、使用次数、冲突和 provenance 做全局整理。空结果是正常结果，失败任务可重试，但不阻塞当前回复。

### 记忆对象

先只实现四类：

- 用户规则/偏好；
- 项目事实/决策；
- 操作经验；
- Run 观察。

每条记忆带来源、适用范围、有效时间、可信度和当前版本。不要一开始实现十几种 type，也不要把原始工具输出全部向量化。

### 读取

```text
recall：混合检索原始证据
  ↓
reflect：生成少量带来源的 Memory Bulletin
  ↓
context：只注入与当前任务直接相关的 bulletin
  ↓
source：需要核对时回到原始 Run
```

不把向量 top-k 结果直接拼入 system prompt。高风险经验只允许显式搜索。相同 Run 使用固定 snapshot。

### 存储

- PostgreSQL：事实源、版本、来源、scope、有效期和关系。
- Qdrant：派生语义索引。
- 关系先只实现 `updates`、`contradicts`、`caused_by`、`result_of`。
- 不先引入 Neo4j、Graphiti 或独立 Memory DB。

### 用户体验

只保留一个经验记忆总开关。用户可以：

- 要求记住。
- 查看来源。
- 修改或删除。
- 标记过期。
- 关闭自动记忆和自动注入。

## 6. 当前 Noesis 实现应该怎样处理

当前代码中可靠的 job、evidence、scope、Run snapshot、索引 outbox 可以保留为基础设施，但以下边界需要重做：

- `experience-only` 不再作为唯一数据模型。
- completed Run 不能只在工具失败时触发提取。
- `RecoveryAdapter` 从主入口降为一种高置信 extractor。
- `search_memory` 不能直接把 raw top-k 变成 system prompt。
- `Memory Cortex` 应该成为 reflect/bulletin 编排层，不只是失败经验注入器。

第一步不是继续加 adapter，而是增加 Session commit / compaction 的统一提取入口，并建立 Noesis Memory 评测集。

## 7. 评测顺序

先测“有没有发现值得记住的东西”，再测“记忆是否带来收益”：

1. 记忆发现覆盖率：显式请求、任务决策、项目进展、失败恢复各自统计。
2. 提取 precision/recall：人工标注候选是否值得保留。
3. 更新时间和冲突准确率：旧事实是否被正确替代。
4. Retrieval precision@k：返回集合是否干净。
5. Context precision：注入的 bulletin 是否真的有用。
6. Agent A/B：任务成功率、重复失败率、工具调用数、token 和 TTFT。
7. 安全：stale、harmful、cross-user、用户关闭后的残留注入。

OpenClaw、Hindsight、Mem0 等公开资料可以帮助设计协议，但不能直接比较其 benchmark 分数；数据集、answer model、judge 和记忆输入协议并不相同。现有研究资料与评测清单见 [`sources/filtered-sources.json`](../sources/filtered-sources.json)。

## 8. 公开榜单和 benchmark

目前没有一个覆盖所有产品的公认“记忆总榜”。需要区分两类评测：MemEval 更偏对话记忆系统；LongMemEval-V2 专门测 Agent trajectory experience memory，并正式包含 Codex baseline。

在其当前 README 的结果中：

| 数据集 | 结果（F1） | 说明 |
|---|---|---|
| LoCoMo | PropMem 0.605，OpenClaw 0.557，Full Context 0.542，Hindsight 0.489，Graphiti 0.416，Mem0 0.344 | 10 conversations / 1,986 QA；OpenClaw 在已测系统中第二 |
| LongMemEval（分层 102 题） | PropMem 0.550，SimpleMem 0.480，OpenClaw 0.244，Full Context 0.222 | 只有四个系统完成该组测试，OpenClaw 的多 Session、时间和知识更新分项明显较弱 |

MemEval 的对话记忆结果仍然有效：LoCoMo 上 OpenClaw 0.557、Hindsight 0.489、Mem0 0.344；但它不代表 coding-agent trajectory memory。[MemEval README results](https://github.com/ProsusAI/MemEval)

### 8.1 LongMemEval-V2：Codex 已有真实外部效果基线

LongMemEval-V2 是目前最贴近 Noesis 的公开评测。它有 451 道人工标注问题、5 类能力（static state、dynamic state、workflow、gotchas、premise awareness），每题最多 500 条历史 trajectory，最大约 115M tokens；评测协议是 `Insert(trajectory) → Query(question) → bounded context → fixed reader`，同时报告 accuracy 和 query latency。[LongMemEval-V2 repo](https://github.com/xiaowu0162/LongMemEval-V2) [LongMemEval-V2 paper](https://arxiv.org/html/2605.12493)

在相同评测协议下，Codex baseline 结果为：

| 方法 | Small accuracy | Small latency | Medium accuracy | Medium latency |
|---|---:|---:|---:|---:|
| RAG query→slice+notes | 51.0% | 0.2s | 45.9% | 0.3s |
| **Codex** | **69.9%** | 177.2s | **68.7%** | 185.8s |
| AgentRunbook-C | 74.9% | 108.3s | 70.1% | 139.9s |

这证明 Codex 作为“文件系统上的 Agentic memory controller”确实很强：它明显超过普通 RAG，尤其擅长从长、杂乱、包含失败轨迹的历史中主动找证据。但它的 query latency 很高。AgentRunbook-C 的提升并不是换了更强模型，而是加了三类结构：

- 轨迹 manifest，让 Agent 先缩小候选范围；
- helper scripts，让 Agent 按 trajectory/state/span 精确查看证据；
- workflow / procedure / hint notes，把重复经验整理成可直接检索的文件。

更重要的是：这个 Codex baseline **不是原生 Codex Memories Phase 1/Phase 2**。评测代码把 trajectory 写入文件，查询时直接调用 `codex exec`，要求它读取文件并输出结构化 evidence spans；因此它证明的是“Codex + file-based agentic retrieval”有效，而不是原生后台记忆 pipeline 已经被公开证明。[Codex baseline implementation](https://github.com/xiaowu0162/LongMemEval-V2/blob/main/memory_modules/codex.py)

LongMemEval-V2 的后续 AgentRunbook-C V2 又加入了由 Codex 驱动的 retrieval-strategy consolidation：每次查询后提取可复用的搜索策略、路径和已排除方向，写入下一次查询使用的策略文件；Small / GPT-5.4-mini 结果达到 75.61%，高于 vanilla Codex 的 69.90%。这说明“记忆不仅保存事实，也保存检索和操作策略”对 Agent memory 有实际收益。[AgentRunbook-C V2](https://xiaowu0162.github.io/longmemeval-v2/agentrunbook-c-v2/)

因此本报告中的 Codex 8.6 是架构适配分，LongMemEval-V2 的 69.9%/68.7% 才是目前可引用的外部效果结果。两者不能混用。

## 9. 最终判断

如果目标是 Noesis 这种 coding-agent：主方案应采用 **Codex 的文件型 Agentic retrieval + Codex Memories 的两阶段后台生命周期**。LongMemEval-V2 已证明前者的效果；Codex 开源仓库提供了后者的 job、lease、consolidation、usage 和 citation 机制。

OpenClaw 作为第二参考：借它的 provenance、Dreaming、compaction flush 和人工可审查体验；它更像通用个人 Agent 的产品基线。

如果只能选一个读取思想：选 **Hindsight**，因为 `recall → reflect → context` 比 raw vector top-k 注入更适合长任务 Agent。

Noesis 的实现顺序应是：先实现 LongMemEval-V2 已验证的“文件化 trajectory + manifest + helper + Agentic query”读取骨架，再接入 Codex 风格的两阶段异步整理；PostgreSQL 保存事实源和 Run provenance；Hindsight 的 bulletin 负责限制注入上下文；OpenClaw 的 provenance 和人工审查负责治理。当前“只在工具失败时提取”的入口应删除。

实现前必须固定以下硬约束：

- **覆盖**：每个完成 Run 都进入候选队列；明确记忆、compaction、Session 结束和失败恢复都只是不同信号，不互相替代。
- **大输入**：先做 token 预算和敏感信息过滤，再按消息/工具调用边界切块；超限时保留首尾、决策、失败/修复和验证片段，禁止整段失败后静默丢弃。
- **质量**：候选先落库并保留 `succeeded_no_output`、`failed`、`needs_review` 等状态；只有通过 schema、scope、冲突、敏感信息和证据检查的候选才能进入可召回层。
- **选择**：使用次数只能作为排序信号，不能成为新记忆的必要条件；新候选必须有冷启动窗口，否则 Codex 式 usage-aware selection 会让从未被召回的记忆永远没有机会被召回。
- **读取**：先检索证据，再生成短 bulletin；注入必须带 memory id、source run 和 snapshot；raw evidence 只能按需展开。
- **可观测**：用户能看到候选总数、成功/空结果/失败/超限、最后整理时间、注入次数和关闭开关后的残留检查结果。
- **效果门禁**：先用固定的 Noesis RunMemory 数据集跑 extraction precision、recall、scope/supersession、retrieval precision@k、memory-on/off 任务成功率和 token 成本；未达到门槛不进入全量实现。

## 10. 限制

- 分数是文档/源码分析，不是统一环境 benchmark。
- OpenClaw、OpenViking、Hermes 的官方资料描述能力边界，但不同版本可能变化。
- HydraDB、Kumiho、Supermemory 等部分性能和效果属于产品方声明，未纳入主要评分依据。
- Noesis 目标设计尚未实现，目标分不能用于项目成果宣传。
