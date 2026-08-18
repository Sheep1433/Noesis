# Agent Memory 研究交叉验证矩阵

| 设计问题 | 产品/论文 A | 产品/论文 B | 一致结论 | Noesis 采用方式 |
|---|---|---|---|---|
| 核心记忆是否常驻 | Letta memory blocks；Spacebot identity context | Hermes MEMORY/USER；Claude Code MEMORY.md | 稳定身份和规则应少量常驻 | 保留 USER.md/AGENTS.md，但限制长度和职责 |
| 大量历史如何读取 | Letta files/archival；OpenClaw semantic_search | Hindsight recall；Graphiti search | 历史记忆按需检索 | `recall` 工具返回证据，不默认全量注入 |
| 事实变化如何处理 | Graphiti validity windows | HydraDB Git-style versioning、Kumiho revision | 不能只 append，也不能静默覆盖 | memory revision + `updates/contradicts` |
| 检索是否只靠向量 | Spacebot vector + FTS + graph | Hindsight semantic + BM25 + graph + temporal | 混合信号更适合精确、关系和时间问题 | PostgreSQL metadata + Qdrant vector + relation expansion |
| 是否应该每轮注入 raw recall | Hindsight 2026 spec 记录 raw recall 回归 | Spacebot Cortex / Hindsight mental models | 先综合，再注入 | Reflector 生成 Bulletin，原始证据按需读取 |
| 何时写入 | Mem0 async add | Hermes sync_turn、Spacebot idle reflection | 实时纠正，后台学习 | explicit remember 同步；Run 后 Reflect 异步 |
| 记忆是否要连接 Agent 经验 | Spacebot worker/branch/cortex | Hindsight experiences/mental models | 现代 Agent memory 不只保存用户偏好 | 以 run_id、tool call、artifact 作为 provenance |
| 是否需要文件作为机器事实源 | OpenClaw/Claude Code 采用文件 | Spacebot 明确放弃 Markdown 作为 memory source | 两条路线都存在 | 文件仅保留人工 Identity/Policy，不保存机器经验主状态 |
| 是否必须独立图数据库 | Graphiti/Zep 使用 temporal graph | Spacebot 用 SQLite 关系和 LanceDB | 图语义可先在现有数据库实现 | PostgreSQL relation 表，暂不引入 Neo4j |
| 评测什么 | LongMemEval/LoCoMo | MemoryAgentBench/RECON/PrecisionMemBench | QA、更新、关系、检索精度要拆开 | public benchmark + Noesis RunMemory set |

## 主要冲突和解释

### 文件优先 vs 数据库优先

这是产品目标不同造成的差异。Claude Code、Codex 和 Hermes 优先考虑人可编辑、迁移和本地可审查；Spacebot、Hindsight、Graphiti 和 HydraDB 优先考虑多类型、检索、时序和自动维护。Noesis 的目标是突出长任务 Runtime，因此机器经验应进入结构化状态层；`USER.md/AGENTS.md` 只保留人工控制内容。

### Graph vs Vector

向量适合语义召回，图或关系适合“谁导致了什么”“哪个决策被什么替代”“某事实在当时是否有效”。Noesis 不需要把所有记忆都图化，只需保留影响简历叙事的四类关系。

### 高召回 vs 高精度

LongMemEval 这类 QA 可能奖励把证据放进 top-k；PrecisionMemBench 直接惩罚返回过多无关结果。Noesis 应将 retrieval recall 和 context precision 分开报告，并加入固定 token budget。
