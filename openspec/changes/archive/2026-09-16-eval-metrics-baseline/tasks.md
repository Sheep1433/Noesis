# Tasks: eval-metrics-baseline

## 1. 统一评测基建（先行，其余线依赖）

- [x] 1.1 新增 `backend/evals/manifest.py`：manifest schema v1（eval_line/tag/generated_at/被测模型/judge 模型/embedding+rerank 模型/数据集路径+题数+种子/配置快照/token 与估算费用/git sha）与读写工具函数，附单测 `tests/test_eval_manifest.py`
- [x] 1.2 新增共享 judge 入口约定：`--judge-model-id` 参数解析与「judge ≠ 被测模型」校验函数（放 `evals/manifest.py` 或独立 `evals/judge.py`），附单测
- [x] 1.3 实现 `results/<tag>/` 四件套产物约定（manifest/raw/summary.json/summary.md）的共享写入助手与「已存在 tag 拒绝覆盖」校验，附单测
- [x] 1.4 实现人工抽检清单落盘（`manual_review_queue.json`，固定 10% 抽样、种子进 manifest），附单测

## 2. RAG 检索线修复与补强（`evals/kb/`）

- [x] 2.1 提取 `evals/kb/metrics.py` 纯函数模块：`gt_rank`/Recall@K/**MRR（miss 记 0 进分母）**/**nDCG@10（多 GT 折算）**/阈值模拟（GT 存活窗口参数化，默认 10）/bootstrap 95% CI（固定种子），全部附单测（`tests/test_eval_kb_metrics.py`，含 miss 场景与多 GT 场景）
- [x] 2.2 重写 `erb.py` 汇总与落盘：改用 metrics 纯函数、输出 `evals/kb/results/<tag>/` 四件套、manifest 记录 embedding/rerank 模型与集合配置与成本；删除 `verbose` 死参；负样本池为空时不再抛异常
- [x] 2.3 `erb.py` 接入 `eval_langfuse_run`（eval_line=kb）
- [x] 2.4 冒烟验证：`--sample 2` 与 `--sample 10 --ablation` 各跑一次，核对落盘产物与指标（含 MRR 分母、阈值窗口口径）

## 3. Agent E2E 判卷、引用与归因（`evals/agent/rag/`）

- [x] 3.1 新增 `evals/agent/rag/judge.py`：gold_answer 三档判卷（采纳/部分采纳/不采纳，judge 模型经 1.2 入口），prompt 版本进 manifest，附 fake-LLM 单测（`tests/test_eval_agent_rag_judge.py`）
- [x] 3.2 runner 增量落盘与断点续跑：逐题 append `raw.jsonl`、启动按 sample_id 跳过已完成题、error 题不中断整批、`--retry-failed` 只重跑 error 题，附单测
- [x] 3.3 数据集落地：从 ERB `questions.jsonl` 生成 `evals/agent/rag/fixtures/erb211.jsonl`（query + expected_doc_ids + gold_answer + answer_facts），替换 replace-me 占位样本
- [x] 3.4 新增 `evals/agent/citation.py`：引用格式遵循率与引用正确率（确定性，含 `file:` 伪协议头/中文文件名 URL 编码回归断言）+ 事实可溯源率（judge 判 answer_fact 支撑），共享同一次 agent run，附单测
- [x] 3.5 新增 `evals/agent/rag/attribution.py`：对不采纳题关联检索命中（`kb/results/<tag>/raw.json` 同题，缺则现场补一次检索）+ 工具轨迹，确定性规则归因三类 + 待人工复核兜底，产出 `attribution.md`，附单测
- [x] 3.6 `__main__.py` 接 manifest/落盘/`--judge-model-id`/`--retry-failed`，exit code 语义复核（error 题与判卷结果分开呈现）
- [x] 3.7 实跑验证：`--sample 10` 抽样跑（被评 GLM-5.3-Flash + 独立 judge），核对增量落盘、断点续跑（中途 Ctrl-C 重跑）、引用三指标与归因报告产出

## 4. 压缩评测口径重定向（`evals/compression/`）

- [x] 4.1 `rubric.py` 新增 2/1/0 recall 判卷 prompt 与解析（judge 可见 reference、作答方不可见）；五维 rubric 保留为诊断维度只进 raw；附单测
- [x] 4.2 `grader.py`：probe 作答与 judge 分别经独立模型入口（judge 走 1.2 校验）；judge 解析失败重试一次、仍失败剔除并单列 `judge_parse_error_rate`，附单测
- [x] 4.3 `__main__.py` 增加 `--arms`（默认 compressed,uncompacted）与 `--policies`（压缩配置预设档）：uncompacted 臂跳过压缩直接闭卷作答同一题库；summary 首行 recall% @ retained tokens 与任务保持率 Δ；旧五维 headline 字段移除，附单测
- [x] 4.4 `driver.py`：摘要识别改为依赖 `lc_source=summarization` 结构化标记（无标记显式置空并标注，删除英文 "summary" 子串与长度启发式）；token 计数统一为 chars/4（content + tool_calls 序列化）并写进 manifest 口径字段，附单测
- [x] 4.5 新增 `evals/compression/export_session.py`：从本地 Claude Code 会话（`~/.claude/projects/` JSONL）聚合导出 transcript + 脱敏（邮箱/key/密钥模式/绝对路径占位符替换）；导出细节对一两个真实长会话确认后固化，产物人工过审后入 `fixtures/real/`
- [x] 4.6 新增 `evals/compression/gen_probes.py`：LLM 从将被压缩区域生成事实 recall 题库（question + reference_answer），按 transcript 内容 hash 缓存至 `probes/`，附 fake-LLM 单测
- [x] 4.7 合成 fixture 保留为零 LLM 冒烟档：可种植事实断言路径打通（不依赖真实数据与 LLM 即可回归），附集成冒烟
- [x] 4.8 README 更新：口径断代说明（新 headline 从本 change 起算、旧 results 不迁移）、评测集来源与脱敏流程、臂与策略参数用法

## 5. 记忆召回评测补强（`evals/agent/memory/`）

- [x] 5.1 LongMemEval（v1，S 子集）接入：数据下载与导入脚本——会话历史按题灌入 Noesis MemoryStore（每题隔离评测用户、幂等、不碰真实用户数据），题目与 `answer_session_ids` 标注转换为评测数据集；导入脚本以 fake store 单测覆盖幂等与隔离
- [x] 5.2 runner 升级为三层指标：答案正确性（对齐 LongMemEval 评测协议，judge 走 1.2 入口）、条目级 recall@k / precision@k（`search_memory` 返回条目 vs `answer_session_ids`）、行为级召回（是否主动调用 `search_memory`，从 run trace 判定）；负例 = 拒答类题目 + 自建配对负例（无线索提问双条件断言：未调用且未引用）
- [x] 5.3 `__main__.py` 接 manifest/落盘四件套；新增 `tests/test_eval_agent_memory.py`（导入幂等与三层指标计算纯函数）；现有 4 题自建场景保留为冒烟集
- [x] 5.4 实跑验证：LongMemEval S 子集抽样（≥30 题）跑一轮（被评 GLM-5.3-Flash + 独立 judge），核对三层指标与负例判定

## 6. 规格与文档收口

- [x] 6.1 `backend/evals/README.md` 更新：统一产物结构与 manifest 说明、各线新参数、记忆评测集为自建合成集的边界说明
- [x] 6.2 核对 `openspec/specs/offline-evals/spec.md` 与本 delta 的 REMOVED/MODIFIED 清单一致（归档时对齐），跑 `python3 scripts/verify-md-links.py`
- [x] 6.3 非平凡决策补决策记录（`docs/decisions/`）：压缩 headline 口径切换（含被否的「保留五维做 headline」）、重型 memory 条款移除裁决（含理由与恢复路径）

## 7. 收尾验证

- [x] 7.1 全量单测绿：`cd backend && uv run pytest tests/ -q`（含新增各 `test_eval_*`）
- [x] 7.2 基线首跑：四条线各以 `--tag baseline-2026-09` 落盘一轮真数字（RAG 全量 211+20、E2E 抽样起步、压缩真实 fixture、记忆全场景），产物进 `results/` 并记录成本
- [x] 7.3 code-review（两轴：仓库规范 + 本 change spec），按审查经济学过 blocker
