# 决策：评测体系 headline 口径与重型 memory 规格条款收口

状态：implemented
日期：2026-09-03

## 问题

评测脚本四条线各自为政：`evals.kb.erb` 指标只打 stdout 且 MRR 分母漏 miss；
Agent E2E 没有判卷（只有确定性 source_recall，数据集是占位符）；
压缩评测以五维 0–5 文本质量分做 headline、无对照组，无法回答「压缩丢了多少任务能力」；
记忆召回只有 4 题二值断言。同时 `openspec/specs/offline-evals/spec.md` 中存在
8 条无任何实现的设想性 memory harness 条款（capture/extraction/consolidation 分指标、
数值 release gate、冻结快照 paired A/B、缓存评测），规格冒充现状。

## 决策

eval-metrics-baseline change（四条线统一基建与口径重定向）：

1. **压缩 headline 从五维 rubric 分换为 recall% @ retained tokens**（judge 2/1/0
   判卷），五维降级为只进 raw 的诊断维度；必须有 uncompacted 对照臂，逐 fixture
   报任务保持率 Δ。旧五维 headline 是口径断代点，旧 results/ 不迁移。
2. **Agent E2E 引入 gold_answer 判卷**（三档，judge 与被测模型强制分离），
   同一次 run 顺带产出引用溯源三指标（格式遵循率 / 引用正确率确定性，
   事实可溯源率 judge），失败题按确定性规则归因到「检索没召回 / 工具行为异常 /
   推理错 / 待人工复核」。
3. **记忆评测数据集从自建合成改为 LongMemEval（v1，S 档）公开数据接入**：
   haystack 会话按题导入隔离评测用户的记忆存储，三层指标（答案正确性 /
   条目级 recall@k·precision@k / 行为级召回）；负例全部自建配对（S 档无拒答题型）。
4. **统一基建**：四线共用 manifest schema v1（模型/数据集/种子/配置/token/git sha）、
   `results/<tag>/` 四件套产物、tag 复用拒绝、judge 分离校验、10% 人工抽检清单。
5. **offline-evals 主规格 8 条无实现 memory 条款整体移除**，由与实现对齐的
   「记忆召回行为评测」Requirement 承载现状。

## 备选方案

- **压缩 headline 保留五维 rubric**：被否。五维混入文本质量维度（continuity 等），
  主观且不稳；任务对照（闭卷 recall + uncompacted 上限）才可回归，这正是五维
  做不了的。五维保留为诊断而非删除，因为失败定位时维度明细有价值。
- **promptfoo / Ragas 等框架承载 judge 类评测**：被否。判卷环节约 30 行代码，
  而压缩多臂对照、KB 管线参数、行为断言都在框架形状之外；引入框架只为省
  这 30 行，代价是 Node 工具链与 judge 语义游离在统一校验之外。`evals.case`
  继续用 promptfoo（该场景形状吻合）。
- **压缩评测集用 hermes 的长会话**：被否——transcript 不随仓库发布（真实会话
  数据），物理上拿不到。改为用其建集方法：本地 Claude Code 会话导出脱敏 +
  LLM 从将被压缩区域生成题库（按内容 hash 缓存）。
- **记忆评测集用 LongMemEval-V2**：暂缓。V2 的题干是多模态网页 agent 轨迹
  （最大 1.15 亿 token），灌库与判分成本高一个量级，且对应经验记忆规模化
  能力；v1 的对话形态与记忆库内容分布吻合。P0 基线后重新评估。
- **无实现 memory 条款标注「未实现」保留在活规格**：被否。规格是现状权威，
  设想留在活规格会被误读为已有能力；恢复路径是重新立项，REMOVED 条款里
  写明了这一约定。

## 后果与代价

- 压缩评测旧结果与新口径不可比（刻意的断代）；README 注明断代边界。
- LongMemEval S 档约 270MB 数据不入库（gitignored，脚本下载），首次跑分需外网。
- 工具调用评测（工具选择/参数填充准确率）仍缺位，E2E 归因的「工具行为异常」
  类暂以启发式规则标注——工具调用 golden set 立项后细化。
- Agent 系评测线全部要求显式 `--model-id` 与 `--judge-model-id` 且不得相同，
  judge 档位未定前无法起跑（避免 judge 自评偏差静默进入基线）。
