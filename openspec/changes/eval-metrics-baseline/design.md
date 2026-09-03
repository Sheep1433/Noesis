# Design: eval-metrics-baseline

## Context

评测脚本现状（`backend/evals/`）：四条活跃评测线各自为政——`kb/erb.py` 指标只打 stdout、MRR 分母漏 miss；`agent/rag/` 只有确定性 source_recall、数据集是占位符、全量跑完才落盘；`compression/` 五维 rubric 做 headline、无对照组、probe 与 judge 共用同一模型；`agent/memory/` 四题二值断言。评测体系方案（knowledge-base）已定稿分层框架与指标口径，本 change 把 P0 范围落进仓库。

约束：评测全部自研 runner（不引入第三方评测框架）；评测范围只覆盖 COMMON_QA 与 SUPER_AGENT_QA 两场景；被评侧模型用付费 GLM-5.3-Flash（稳定、无限流中断）；judge 与被评模型必须分离。

## Goals / Non-Goals

**Goals:**

- 每条评测线产出可 diff 的基线：统一 `results/<tag>/` 结构 + manifest（模型、数据集、种子、配置、成本、git sha）。
- 指标正确性：修 RAG 线已知 bug，补 nDCG@10 与 bootstrap 置信区间。
- Agent E2E 有判卷数字（ERB 211 题 gold_answer）与失败归因报告。
- 压缩评测 headline 换为 recall% @ retained tokens，有 uncompacted 对照与策略矩阵，能算任务保持率 Δ。
- 记忆召回有负例与条目级 recall@k / precision@k。

**Non-Goals:**

- 工具调用评测（工具选择/参数填充准确率）——暂缓，等本 change 落地后视 E2E 归因需要再立项。
- 稳定性评测（故障注入）、跨 harness 对比（P1）。
- 压缩后「会话检索兜底」（recovery 分档）——依赖产品侧暂无对应能力，见 Open Questions。
- 统一评测结果 Web 页面——维持各线 CLI/文件产物。

## Decisions

### D1. 统一产物结构：`evals/manifest.py` 新模块 + 各线 `results/<tag>/`

新增共享模块 `backend/evals/manifest.py`：manifest schema v1（`eval_line` / `tag` / `generated_at` / 被测模型 / judge 模型 / embedding+rerank 模型 / 数据集路径与题数与种子 / 配置快照 / input+output tokens 与估算费用 / git sha）。各线产物统一为：

```
results/<tag>/
  manifest.json    # 上面 schema
  raw.json         #（或 raw.jsonl，见 D4）逐题原始记录，含原始分
  summary.json     # 汇总指标（机器可 diff）
  summary.md       # 人读表格；有 --compare-to 时附差值列
```

`kb/erb.py` 从无落盘改为写 `evals/kb/results/<tag>/`；`agent/rag`、`agent/memory`、`compression` 迁到同一结构（compression 已接近，补 manifest 字段）。被否方案：继续各线自定产物格式——方案明确「脚本即资产、diff 即回归报告」，无统一结构则 diff 不可自动化。

### D2. 指标计算提取为纯函数，修 MRR、补 nDCG 与置信区间

`kb/erb.py` 的 `summarize`/`gt_rank` 逻辑提取到 `evals/kb/metrics.py` 纯函数模块：

- **MRR 修正**：未命中题记 0 参与平均（分母 = 全部题数），不再 `if r is not None` 剔除。
- **nDCG@10**：多 GT 文档按标准多相关文档折算（relevant=1，DCG 按排名累加，IDCG 取 GT 数与 k 的较小值）。
- **bootstrap 95% CI**：按题重采样 1000 次、固定种子（manifest 记录种子），对 Recall@K 与 MRR 出区间。
- **阈值表口径统一**：GT 存活窗口从硬编码 top5 参数化为 `--threshold-window`（默认 10，与 Recall 口径一致）。

阈值模拟继续吃 raw.json 的原始 rerank 分离线复算，不为换阈值重调检索 API（既有设计保留）。

### D3. judge 分离：独立取模型入口，校验不与被评相同

共享约定：所有 LLM-as-judge 调用经 `get_llm(model_id=judge_model_id)`，`judge_model_id` 来自 CLI `--judge-model-id` 或配置；runner 启动时校验 `judge_model_id != 被评 model_id`，相同即报错退出。`compression/grader.py` 的 `answer_probe` 与 `grade_probe` 当前共用 `get_llm()` 默认实例，是本决策的直接修复对象。被评侧固定 GLM-5.3-Flash（`--model-id`），judge 档位见 Open Questions。

### D4. Agent E2E：增量落盘 + 断点续跑 + 判卷 + 归因

数据流（`evals/agent/rag/`）：

```
ERB questions.jsonl（211 题）
  → 逐题 run_agentic_rag_sample（被评 GLM-5.3-Flash）
  → 每题完成即 append raw.jsonl（含 sample_id/final_text/引用解析/工具轨迹/usage）
  → 全部完成后汇总 summary
```

- **断点续跑**：启动时读已有 raw.jsonl 的 sample_id 集合，跳过已完成题；网关/超时失败的题保留 error 记录、不中断整批，`--retry-failed` 只重跑 error 题。
- **判卷**：新增 `evals/agent/rag/judge.py`——judge 按 gold_answer 与场景 rubric 打分（采纳/部分采纳/不采纳三档），结果并入该题 raw 记录。任务成功率 = 采纳 + 部分采纳折半（或全量口径，summary 两种都出）。
- **引用三指标**（新建 `evals/agent/citation.py`，同一次 run 内计算，不额外跑 agent）：格式遵循率（citation 契约解析成功率）、引用正确率（引用文档 ∈ 该题 expected_doc_ids）、事实可溯源率（逐条 answer_fact 由 judge 判「引用 chunk 是否支撑」，复用 D3 的 judge 入口）。已知失败模式（`file:` 伪协议头、中文文件名 URL 编码）做成确定性回归断言。
- **失败归因报告**：`evals/agent/rag/attribution.py`——对 judge 判不采纳的题，关联该题的检索命中记录（`kb/results/<tag>/raw.json` 同题数据；无则现场补一次检索）+ 工具轨迹，按确定性规则分类：检索未命中 GT → 「检索没召回」；命中 GT 但未调用 KB 工具 / 调用了替代工具 → 「工具行为异常」；命中且工具正常但回答错 → 「推理错」；参数级错误本 change 无 golden set，并入「工具行为异常」附原始轨迹供人工复核。产物为 `attribution.md`（四类计数 + 逐题明细）。被否方案：等工具调用评测立项后再做归因——那样 E2E 分数在 P0 仍不可解释，违背「组件层解释系统层」的分层原则。

### D5. 压缩评测口径重定向与评测集重建

`evals/compression/` 的改动：

- **judge 换 2/1/0**：`rubric.py` 新增 recall 判卷 prompt（correct=2 / partial=1 / wrong=0，judge 可见 reference、作答方不可见）；`recall% = Σscore / (2N)`。五维 rubric 保留为诊断维度，只进 raw 记录不进 headline。被否方案：直接删五维——失败定位时维度明细有诊断价值，headline 与诊断分离即可。
- **对照组**：`--arms` 参数（默认 `compressed,uncompacted`）。uncompacted 臂跳过 compress、直接闭卷作答同一题库，作为 recall 上限；任务保持率 Δ = compressed recall% − uncompacted recall%，进 summary 首行。
- **策略矩阵**：`--policies` 把 `compress_options` 预设档（current / aggressive / 长 keep 等）参数化，每档独立成臂。
- **judge 解析失败处理**：重试一次后仍失败则该题标记 invalid、单列 `judge_parse_error_rate`，从 recall% 分母剔除（当前实现记 0 分进中位数，把格式故障混成质量差）。
- **评测集来源**：新增 `evals/compression/export_session.py`——从本地 Claude Code 会话（`~/.claude/projects/` 的 JSONL 逐事件记录）聚合导出长会话 transcript（messages JSON），脱敏规则（邮箱/token/密钥模式/绝对路径替换为占位符）后落 `fixtures/real/`；导出细节（会话挑选标准、事件流到 messages 的聚合规则）在实施期对着一两个真实长会话确认后再固化；`evals/compression/gen_probes.py`——由 LLM 从「将被压缩区域」生成事实 recall 题库（question + reference_answer），按 transcript 内容 hash 缓存到 `probes/`，保证可复现。旧三个合成 fixture 保留为零 LLM 冒烟档（可种植事实断言，不依赖真实数据）。token 计数统一为 chars/4（content + tool_calls 序列化长度），口径写进 manifest。
- **摘要识别**：`driver.py` 的 `_extract_summary_text` 靠英文 `"summary"` 子串启发式，改为依赖 middleware 写入的结构化标记（`lc_source=summarization`），无标记时显式置空并在结果中标注，不再猜测。

### D6. 记忆召回：LongMemEval 接入与三层指标

`evals/agent/memory/` 从「4 题自建场景」升级为「公开数据集接入 + 行为断言自研」：

- **数据集**：LongMemEval（v1，S 子集起步）。其会话历史（HuggingFace 公开，S 子集约 115K token/题）按题导入 Noesis MemoryStore——每题一个隔离评测用户，导入幂等、不碰真实用户数据；题目与自带的 `answer_session_ids` 标注（答案在哪些会话里）转换为评测数据集。
- **三层指标**：① 答案正确性——对齐 LongMemEval 的评测协议（judge 判卷，judge 分离照 D3）；② 检索命中——`search_memory` 返回条目对 `answer_session_ids` 的 recall@k / precision@k（条目级）；③ 行为级召回——需要记忆线索的题，Agent 是否主动调用 `search_memory`（从 run trace 判定，Noesis 特有断言，公开基准均不覆盖此层）。
- **负例**：LongMemEval 的拒答类题目（abstention）为底，另保留少量自建配对负例（同一批导入记忆、同领域但无线索的提问）——负例断言「未调用 `search_memory` 且回答未引用已导入记忆」双条件。
- **v2 不作起步**：LongMemEval-V2（2026-08 更新）把评测对象换成了多模态网页 agent 轨迹（最大题干 1.15 亿 token、500 条轨迹），灌库与判分成本高一个量级，且其「定制环境里的熟手经验」（工作流知识、环境陷阱）对应的是经验记忆规模化能力——列为后续升级候选，接入时机在 P0 基线出来后评估。
- 现有 4 题自建场景保留为冒烟集（不依赖外部数据即可回归 runner），`expect_label_surfaced` 的子串断言随三层指标一并重做。

### D7. offline-evals 主规格收口

`openspec/specs/offline-evals/spec.md` 中与实现脱节的 memory 条款（capture/extraction/consolidation 分指标、数值 release gate、冻结快照 paired A/B、缓存评测——均无对应 harness 实现）随本 change 改写为与 `evals.agent.memory` 实际能力对齐的 Requirement；未实现设想不留在活规格里冒充现状。归档时主规格与 delta 对齐。

## Risks / Trade-offs

- [付费跑分成本失控] → manifest 每次记录 token 与估算费用；`--sample` 抽样冒烟先行，全量须显式 `--all`；单线跑分前打印预估题数与预算。
- [judge 本身的噪声污染基线] → judge 分离 + 固定 judge prompt 版本（进 manifest）；人工抽检 10% 样本清单落盘（`manual_review_queue.json`），P2 再算一致性系数，本 change 先把抽检样本产出来。
- [真实会话导出的脱敏遗漏] → 脱敏规则先跑已知模式（邮箱/key/路径），导出产物先人工过一遍再入库为 fixture；导出脚本只读库副本连接串，不碰生产写路径。
- [压缩评测集规模不足（真实长会话数量有限）] → P0 接受「少量真实 + 合成冒烟」组合，summary 标注每臂 fixture 数与来源；题库缓存保证同 fixture 复跑可 diff。
- [旧压缩结果与新口径不可比] → 口径切换是刻意的破坏点：新 headline 从本 change 起算基线，旧 `results/` 不迁移；README 注明断代。
- [记忆负例「未调用 search_memory」可能被 prompt 合理化解释误判为通过] → 负例同时断言回答内容不含种子事实，两条件都过才算负例通过。

## Migration Plan

纯新增评测代码与脚本，无线上 API/SSE 行为变更，无数据迁移。落地顺序：D1 manifest 基建 → D2 RAG 指标 → D4 Agent E2E（含引用与归因）→ D5 压缩 → D6 记忆 → D7 规格收口。回滚 = revert 评测目录改动，无运行时影响。

## Open Questions

- **judge 模型档位**：被评侧 GLM-5.3-Flash 已定；judge 需要不同模型——用户可用模型池里选哪一档做 judge（建议更强档以提高判卷稳定性）？未定前 `--judge-model-id` 为必填参数，不设默认值。
- **E2E 任务成功率的折半口径**：部分采纳是否折半计入（summary 两种口径都出，headline 用哪种待跑完 10% 抽检后定）。
- **压缩 recovery 分档**：需要「压缩后从会话历史检索」的产品能力，当前无对应工具/接口；是否立项属产品决策，不在本 change。
