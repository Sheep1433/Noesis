# eval-metrics-baseline

## Why

评测体系方案已定稿（分层框架、指标口径、对标姿态三件事都已裁决），但仓库现状只支撑其中一半：RAG 检索试点有指标 bug 且不落盘、Agent E2E 没有判卷、压缩评测的 headline 口径与方案方向冲突（评摘要文本维度而非压缩后任务保持率，且无对照组）、记忆召回只有 4 题二值断言、引用溯源完全空白。大改动（换 embedding/rerank 模型、改 prompt、调压缩策略）目前没有可 diff 的基线，退化只能靠人肉观察发现。

## What Changes

- **RAG 检索（`evals.kb.erb`）修复与补强**：修 MRR 分母漏 miss 的虚高 bug；补 nDCG@10 与 bootstrap 置信区间；结果落盘 `results/<tag>/`（manifest + raw + summary），不再只输出 stdout；接入评测 Langfuse。
- **新建引用溯源评测**：复用 ERB 211 题的 `answer_facts` + `gold_answer`，与 Agent E2E 共享同一次 agent run，产出引用格式遵循率、引用正确率（确定性）与事实可溯源率（LLM judge）。
- **Agent E2E 补判卷与归因**：`evals.agent.rag` 增加 gold_answer LLM-as-judge（judge 与被评模型分离）；逐题增量落盘与断点续跑；E2E 失败题自动关联该题检索命中与工具轨迹，归到「检索没召回 / 工具选错 / 参数填错 / 推理错」四类。通用 Agent 场景复用既有 browsecomp 线，不新建数据集。
- **压缩评测口径重定向**：headline 从五维 0–5 rubric 分改为 **recall% @ retained tokens**（judge 2/1/0 判卷），五维降级为失败诊断维度；补 uncompacted 对照组与多策略矩阵（`compress_options` 参数化），产出任务保持率 Δ；judge 与 probe continuation 模型分离；judge 解析失败重试后剔除并单列，不再记 0 分。
- **压缩评测集重建**：放弃手搓合成 fixture 为主的路线，改为从本地 Claude Code 会话（`~/.claude/projects/`）导出脱敏 transcript + LLM 生成事实 recall 题库（题库缓存保 reproducibility）+ 保留可种植事实的合成 fixture 做零 LLM 冒烟。
- **记忆召回评测补强**：数据集从 4 题自建场景升级为 LongMemEval（v1，S 子集）公开数据接入——会话灌入 Noesis 记忆存储、题目跑 Noesis Agent；指标分三层：答案正确性（对齐其评测协议）、条目级 recall@k / precision@k、行为级召回（是否主动调用 `search_memory`）；负例由其拒答类题目与自建配对场景构成。
- **统一评测基建约定**：所有线统一 manifest schema（模型版本/数据集/种子/配置快照/token 成本/git sha）、统一 `results/<tag>/` 产物结构、judge 模型独立配置入口。
- **规格卫生**：`offline-evals` 主规格中与实现脱节的重型 memory 条款（数值 release gate、冻结快照 harness 等无对应实现者）按现状收口。

### 非目标

- **工具调用评测**（工具选择准确率/参数填充正确率）暂缓，不在本 change 范围。
- **稳定性评测**（故障注入）维持排除。
- 同模型跨 harness 的 Claude Code 对比属 P1，本 change 只把 Noesis 自身基线数字跑出来。
- 不引入 promptfoo / Ragas 等第三方评测框架，全部自研 runner（既有约定不变）。

## Capabilities

### New Capabilities

（无——本 change 全部落在既有 `offline-evals` 能力内。）

### Modified Capabilities

- `offline-evals`：
  - ERB 检索基准 Requirement 扩充（nDCG、置信区间、落盘 manifest、Langfuse）
  - 新增引用溯源评测 Requirement
  - 新增 Agent E2E 判卷与失败归因 Requirement
  - 消息压缩评测 Requirement 改写（headline 口径、对照组、题库来源、judge 分离）
  - 记忆召回评测 Requirement 改写（负例、检索级指标、数据集规模）
  - 新增统一评测基建 Requirement（manifest/落盘/成本/judge 分离）
  - 无实现支撑的重型 memory harness 条款收口

## Impact

- 代码：`backend/evals/kb/erb.py`（重写汇总与落盘）、`backend/evals/agent/rag/`（judge、增量落盘、引用指标、归因报告）、`backend/evals/compression/`（口径重定向、对照组、题库生成、fixture 重建）、`backend/evals/agent/memory/`（负例与检索级指标、数据集扩容）、`backend/evals/agent/runtime.py`（如需工具入参捕获）、共享 manifest 模块（新增）。
- 数据：`evals/compression/fixtures/` 与 `probes/` 结构变更（真实会话导出 + 生成题库，旧合成 fixture 保留为冒烟档）；新增会话导出脱敏脚本。
- 模型成本：Agent E2E 211 题全量用付费模型（GLM-5.3-Flash）跑被评侧，judge 每题一次；压缩线双跑（压缩组 + uncompacted 对照组）。manifest 记录每次成本。
- 测试：新增各线纯函数单测（指标计算/阈值模拟/manifest/归因分类），沿用 `tests/test_eval_*` 命名。
- 不影响任何线上 API/SSE 行为；评测入口 CLI 参数有增（`--tag`、`--judge-model-id` 等），旧参数保持兼容。
