# agent-offline-eval（Delta）

本 delta 彻底重构 `openspec/specs/agent-offline-eval/spec.md`：从「DeepResearch 摘取版 WildClawBench」升级为 **WildClawBench 全量 + BrowseComp + OpenHands Index 五柱** 的统一 Agent 离线评测能力。

## ADDED Requirements

### Requirement: 三套件 Benchmark Taxonomy

系统 SHALL 在 `backend/evals/agent/suites/` 维护三套独立评测套件，并在 `backend/evals/agent/shared/taxonomy.yaml` 记录与上游 benchmark 的对照关系：

| suite id | 上游 | 规模（full） | 测什么 |
|----------|------|--------------|--------|
| `wildclaw` | WildClawBench | 60 题 / 6 类 | 长程真实工具、多步编排、环境副作用 |
| `browsecomp` | BrowseComp (OpenAI) | 1266 题 | 持久浏览、多跳事实检索、短答案 |
| `openhands` | OpenHands Index 五柱 | 按柱扩展 | 软件工程五域：修 issue、绿场开发、前端、测例生成、信息搜集 |

系统 **SHALL NOT** 将 OpenHands 平台本身作为被测 Agent；OpenHands suite 表示 **对齐 OpenHands Index 的任务来源与评分口径**。

#### Scenario: taxonomy 文件可查

- **WHEN** 开发者打开 `evals/agent/shared/taxonomy.yaml`
- **THEN** 系统 SHALL 列出三套件及 OpenHands 五柱与上游 benchmark 名称（含 SWE-Bench Verified、Commit0、SWE-Bench Multimodal、SWT-Bench、GAIA）

#### Scenario: SWE-Bench 归属 OpenHands 柱而非独立第四套件

- **WHEN** 任务 `provenance` 含 `SWE-Bench Verified`
- **THEN** 该任务 SHALL 位于 `suites/openhands/issue_resolution/`，**SHALL NOT** 作为与 `wildclaw`、`browsecomp` 并列的第四顶层 suite

### Requirement: WildClawBench 全量六类覆盖

`suite=wildclaw` 数据集 **SHALL** 覆盖 WildClawBench 全部 **60** 道人工任务，并映射以下 **6** 个 `category`（与上游一致）：

- `productivity_flow`
- `code_intelligence`
- `social_interaction`
- `search_retrieval`
- `creative_synthesis`
- `safety_alignment`

每条任务 **SHALL** 含 `upstream.benchmark=WildClawBench` 与可对照的 `upstream.task_id`。若 Noesis 环境无法提供真实外联服务（邮件、IM 等），manifest **SHALL** 标注 `adaptation: mock_service`，**SHALL NOT** 因适配而删除该题。

#### Scenario: productivity 类任务保留

- **WHEN** 上游 WildClawBench 任务要求操作邮件或日历工作流
- **THEN** Noesis 数据集 **SHALL** 保留对应条目，并使用 mock 或本地替身服务完成可重复评分

#### Scenario: 六类题数之和为 60

- **WHEN** 加载 `suites/wildclaw/manifest.yaml` 且 `tier=full`
- **THEN** 系统 SHALL 解析出 60 条唯一 `item_id`，且每条 `category` 属于上述六类之一

### Requirement: BrowseComp 浏览持久性套件

`suite=browsecomp` **SHALL** 支持 BrowseComp 题型：单条 `query` 为事实寻求问题，`ground_truth.answer` 为短参考答案，`ground_truth.grader` 为 `browsecomp_llm` 或等价实现。

- `tier=dev`：默认子集（**SHALL** ≥50 题），用于日常手动跑分。
- `tier=full`：完整 **1266** 题（可存于 `dataset.full.jsonl` 或本地 cache 路径）。

评分 **SHALL** 使用与 OpenAI simple-evals 等价的 yes/no 判定逻辑；`overall_score` **SHALL** 等于 `accuracy`（正确题数 / 已跑题数）。

#### Scenario: Agent 不可见参考答案

- **WHEN** runner 执行 BrowseComp 条目
- **THEN** 系统 **SHALL NOT** 将 `ground_truth.answer` 注入 Agent prompt 或工具返回值

#### Scenario: dev 与 full 切换

- **WHEN** 开发者执行 `--suite browsecomp --tier full`
- **THEN** 系统 SHALL 加载完整 BrowseComp 数据集而非 dev 子集

### Requirement: OpenHands Index 五柱套件

`suite=openhands` **SHALL** 按以下五柱组织任务，每柱独立 `dataset.jsonl` 与 `pillar` 字段：

| pillar | 上游 benchmark | 能力摘要 |
|--------|----------------|----------|
| `issue_resolution` | SWE-Bench Verified | 修复真实仓库 issue，测试通过 |
| `greenfield` | Commit0 | 由规格从零实现应用 |
| `frontend` | SWE-Bench Multimodal | 前端/UI 相关修改 |
| `software_testing` | SWT-Bench | 生成可复现 bug 的测试 |
| `info_gathering` | GAIA | API/文档调研与短答案 |

报告 **SHALL** 输出每柱 `pillar_score`（该柱已跑条目 `overall_score` 均值）及可配置的 `index_score`（默认五柱等权 20%）。

#### Scenario: 单柱跑分

- **WHEN** CLI 传入 `--suite openhands --pillar issue_resolution`
- **THEN** 系统 SHALL 仅加载 `suites/openhands/issue_resolution/dataset.jsonl`

#### Scenario: index 汇总

- **WHEN** 同一 `--tag` 下五柱均至少跑过 dev 子集
- **THEN** `summary.json` SHALL 含 `openhands_index` 对象，含各 `pillar_score` 与 `index_score`

### Requirement: 分级执行 tier

所有 suite **SHALL** 支持 `tier`：

- `dev`：各 suite 默认可跑子集（WildClawBench 建议每类 ≥2；BrowseComp ≥50；OpenHands 每柱 ≥5）。
- `full`：上游完整规模（WildClaw 60、BrowseComp 1266、OpenHands 按 manifest 扩展）。

CLI **SHALL** 接受 `--tier dev|full`，默认 `dev`。

#### Scenario: 默认 dev 控制成本

- **WHEN** 开发者执行 `uv run python -m evals.agent --tag smoke --suite all` 且未传 `--tier`
- **THEN** 系统 SHALL 使用 `tier=dev` 过滤条目

### Requirement: Agent 画像 agent_profile

数据集条目 **MAY** 声明 `agent_profile`：

- `deep_research`（默认）：`DeepResearchAgent`，`qa_type=DEEP_RESEARCH_QA`。
- `coding`：同一沙箱 execute 栈的代码向任务（OpenHands 前四柱）；**SHALL NOT** 误用 `evals.case` 的 `case_graph`。

runner **SHALL** 按 `agent_profile` 选择调用路径；未声明时 **SHALL** 使用 `deep_research`。

#### Scenario: BrowseComp 使用 deep_research

- **WHEN** `suite=browsecomp` 条目未声明 `agent_profile`
- **THEN** runner SHALL 调用 `DeepResearchAgent`

#### Scenario: SWE 柱使用 coding 画像

- **WHEN** `suite=openhands` 且 `pillar=issue_resolution`
- **THEN** 条目 **SHALL** 声明 `agent_profile=coding` 或等价代码向配置

### Requirement: 多套件 CLI 与 suite=all

`evals.agent` CLI **SHALL** 支持：

- `--suite wildclaw|browsecomp|openhands|all`
- `--pillar <name>`（仅 `openhands`）
- `--tier dev|full`
- 既有 `--tag`、`--item-id`、`--limit`、`--compare-to`

环境变量：`NOESIS_AGENT_EVAL_SUITE`、`NOESIS_AGENT_EVAL_TIER`、`NOESIS_AGENT_EVAL_PILLAR`。

#### Scenario: 跑全部 dev 套件

- **WHEN** 执行 `uv run python -m evals.agent --tag nightly --suite all --tier dev`
- **THEN** 系统 SHALL 顺序或配置顺序执行三套件 dev 子集，并写入统一 `summary.json`

#### Scenario: 单题调试

- **WHEN** 执行 `--suite wildclaw --item-id wc_02_code_sam3_debug`
- **THEN** 系统 SHALL 仅运行该条目一次

### Requirement: 套件化结果路径

每次 `--tag` 运行 **SHALL** 在 `backend/evals/agent/results/<tag>/` 写入：

- `runs/<suite>_<item_id>.json`（含 `suite`、`pillar`、`category`、`tool_stats`、`scores`）
- `summary.json`（含 `by_suite`、`by_pillar`、`openhands_index`（若适用））
- `summary.md`（人类可读，含 `--compare-to` 差分）

#### Scenario: 跨套件对比

- **WHEN** `--compare-to results/baseline` 且 baseline 含多 suite 结果
- **THEN** `summary.md` SHALL 按 suite 与 pillar 分组展示分数差值

### Requirement: BrowseComp 与代码柱专用评分器

除通用规则 criteria 与 `semantic_rubric` 外，系统 **SHALL** 实现：

- `browsecomp_grader`：输入 question、reference answer、model response → yes/no。
- `pytest_pass` / `patch_apply`（OpenHands 代码柱）：在工作区或种子仓库执行测试判定。

#### Scenario: BrowseComp 评分不依赖文件产物

- **WHEN** BrowseComp 条目评分
- **THEN** grader **SHALL** 仅基于最终文本答案判定，**SHALL NOT** 要求工作区文件存在

#### Scenario: Issue resolution 测试判定

- **WHEN** OpenHands `issue_resolution` 条目含 `criteria.type=pytest_pass`
- **THEN** 评分 **SHALL** 在任务工作区执行 pytest 并以退出码作为 pass 依据

## MODIFIED Requirements

### Requirement: Agent 离线评测与测试用例评测目录隔离

系统 SHALL 在 `backend/evals/agent/` 提供 Agent 端到端离线评测，并按 **suite** 组织于 `evals/agent/suites/`。Agent 评测 **SHALL NOT** 与 `evals/case/` 共用 runner 或数据集文件。Agent 评测入口 SHALL 为 `uv run python -m evals.agent`；测试用例评测入口 SHALL 为 `uv run python -m evals.case`（见 `test-case-agent-eval` 规格）。

#### Scenario: Agent 与测试用例评测独立执行

- **WHEN** 开发者运行 `uv run python -m evals.agent --tag dr-baseline --suite wildclaw`
- **THEN** 系统 SHALL 仅加载 `evals/agent/suites/` 下指定套件数据，**SHALL NOT** 读取 `evals/case/`

#### Scenario: 根包不替代场景入口

- **WHEN** 开发者运行 `uv run python -m evals --tag baseline`
- **THEN** 系统 **SHALL NOT** 启动测试用例或 Agent 评测；开发者 SHALL 改用 `evals.case` 或 `evals.agent`

### Requirement: 评测 Agent 运行时

Agent 离线 runner **SHALL** 默认调用 `DeepResearchAgent`（与线上 `qa_type=DEEP_RESEARCH_QA` 一致的工具栈与中间件）。当条目声明 `agent_profile=coding` 时，runner **SHALL** 使用代码向配置（沙箱 execute、读写工作区），**SHALL NOT** 调用 `CommonReactAgent`、`FaultOperationAgent` 或测试用例 `case_graph`。

#### Scenario: 深度研究任务成功执行

- **WHEN** 数据集条目 `query` 非空且 `agent_profile=deep_research`
- **THEN** runner SHALL 在隔离工作区内调用 `DeepResearchAgent`，并记录 `completed`、`latency_ms` 与工具调用统计

#### Scenario: 代码柱任务执行

- **WHEN** OpenHands `issue_resolution` 条目 `agent_profile=coding`
- **THEN** runner SHALL 在带 `workspace_seed` 的隔离环境中完成代码修改，并收集 `tool_stats` 与测试命令退出码

### Requirement: 工作区隔离与种子资产

每条评测任务 SHALL 在运行前获得独立工作区目录（位于 `.data/agent_workspace/` 下 eval 专用路径）。若条目声明 `workspace_seed`，runner SHALL 将种子目录复制到该工作区后再启动 Agent。单次 run 内不同 `item_id` **SHALL NOT** 共享可写工作区。BrowseComp 纯问答条目 **MAY** 省略 `workspace_seed`。

#### Scenario: 带种子代码的任务

- **WHEN** WildClawBench 或 OpenHands 条目含 `workspace_seed`
- **THEN** runner SHALL 在 Agent 启动前将种子文件复制到本次任务工作区

#### Scenario: BrowseComp 无工作区

- **WHEN** `suite=browsecomp` 且条目无 `workspace_seed`
- **THEN** runner **MAY** 使用空工作区或仅会话上下文，**SHALL NOT** 因此失败

### Requirement: 混合评分在 Agent 结束后执行

系统 SHALL 在 Agent 运行结束后执行评分；`ground_truth` **SHALL NOT** 在 Agent 执行期间暴露。评分顺序：

1. 确定性 `criteria`（含 `pytest_pass` 等套件扩展类型）
2. 工作区副作用检查（WildClaw / 代码柱）
3. `semantic_rubric` 的 LLM Judge（若存在）
4. BrowseComp grader（若 `grader=browsecomp_llm`）

WildClaw / OpenHands 长程任务：`overall_score = 0.7 × rule_pass_rate + 0.3 × semantic_pass_rate`（无 semantic 时仅用 rule）。BrowseComp / GAIA 短答案：`overall_score = accuracy`。

Judge **SHALL** 使用 `get_llm()`，**SHALL NOT** 提供 mock 开关。

#### Scenario: 规则项判定文件产物

- **WHEN** `ground_truth.criteria` 含 `type=file_exists` 且 Agent 已在约定路径创建文件
- **THEN** 评分结果 SHALL 将该 criterion 记为 `passed=true`，且不调用 LLM

#### Scenario: 语义 rubric 调用真实 LLM

- **WHEN** 条目含非空 `semantic_rubric` 且规则项已执行完毕
- **THEN** 系统 SHALL 使用 `get_llm()` 进行 Judge

### Requirement: CLI 过滤与手动执行

`evals.agent` CLI SHALL 支持 `--tag`（必填）、`--suite`、`--tier`、`--pillar`、`--item-id`、`--limit`、`--dataset`；环境变量 `NOESIS_AGENT_EVAL_*`。全量评测 **SHALL NOT** 作为 CI 必需步骤。

#### Scenario: 调试单题

- **WHEN** 开发者执行 `uv run python -m evals.agent --tag debug --suite wildclaw --item-id wc_02_code_sam3_debug`
- **THEN** 系统 SHALL 仅运行该条目一次并输出结果

## REMOVED Requirements

### Requirement: 数据集排除生活类 WildClawBench 任务

**Reason**：用户要求 WildClawBench **完整** 60 题覆盖，包含 productivity_flow 与 social_interaction；排除生活类与全量目标冲突。

**Migration**：原 `deep_research/dataset.jsonl` 条目迁入 `suites/wildclaw/` 并改 id 为 `wc_*`；新增 productivity / social 类题目与 mock 适配说明见 `manifest.yaml`。

### Requirement: 评测 DeepResearchAgent

**Reason**：由「仅 DeepResearchAgent」扩展为 `agent_profile` 多画像；原 Requirement 标题与范围过窄。

**Migration**：默认行为仍为 `DeepResearchAgent`；OpenHands 代码柱使用 `agent_profile=coding`。

### Requirement: 评测结果持久化与汇总

**Reason**：由单一路径 `runs/<item_id>.json` 扩展为多 suite 命名与 `by_suite` 汇总结构。

**Migration**：旧结果路径仍可读；新跑分使用 `runs/<suite>_<item_id>.json`。

## RENAMED Requirements

（无）
