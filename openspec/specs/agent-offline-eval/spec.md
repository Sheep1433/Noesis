# agent-offline-eval Specification

## Purpose

本能力规定 Noesis **Agent 端到端离线评测**（`backend/evals/agent/`）的验收标准：按 benchmark 分子模块（BrowseComp、WildClawBench、perf 性能回归集）、共用 `DeepResearchAgent` 执行、工作区隔离、评分与结果持久化，以及与 `evals.case`、`evals.compression` 的目录隔离。

## Requirements

### Requirement: Agent 离线评测与测试用例评测目录隔离

Agent 评测 **SHALL NOT** 与 `evals/case/` 或 `evals/compression/` 共用 runner 或结果目录。

根包 `uv run python -m evals.agent` **SHALL** 打印 benchmark 子模块用法说明；实际跑分 **SHALL** 使用下列子模块之一：

| 子模块 | 命令示例 | 用途 |
|--------|----------|------|
| `evals.agent.browsecomp` | `uv run python -m evals.agent.browsecomp --tag bc-smoke` | BrowseComp 官方流程 |
| `evals.agent.wildclaw` | `uv run python -m evals.agent.wildclaw --tag wc` | WildClawBench + Docker grader |
| `evals.agent.perf` | `uv run python -m evals.agent.perf --tag dev` | 性能回归集（自研题集） |

测试用例评测入口 SHALL 为 `uv run python -m evals.case`（见 `test-case-agent-eval`）。

#### Scenario: 根 agent 包不直接跑分

- **WHEN** 开发者运行 `uv run python -m evals.agent` 且无子模块参数
- **THEN** 系统 SHALL 打印上述子模块说明，**SHALL NOT** 自动加载数据集并评分

#### Scenario: Agent 与测试用例评测独立执行

- **WHEN** 开发者运行 `uv run python -m evals.agent.perf --tag dev`
- **THEN** 系统 SHALL 仅加载 `evals/agent/perf/` 相关资产，**SHALL NOT** 读取 `evals/case/testpoints/`

#### Scenario: 根 evals 包不替代场景入口

- **WHEN** 开发者运行 `uv run python -m evals`
- **THEN** 系统 **SHALL NOT** 启动测试用例或 Agent 评测；开发者 SHALL 改用具体子模块

### Requirement: 评测执行 DeepResearchAgent

Agent 离线 runner（各 benchmark 子模块经 `evals/agent/_agent.py` 或等价物）SHALL 调用 `DeepResearchAgent`（与线上 `qa_type=DEEP_RESEARCH_QA` 一致的工具栈与中间件）。**SHALL NOT** 支持切换为其它 qa_type 或 Agent 类。

#### Scenario: 深度研究任务成功执行

- **WHEN** 数据集条目 `query` 非空
- **THEN** runner SHALL 在隔离工作区内调用 `DeepResearchAgent` 完成任务，并记录 `completed`、`latency_ms` 与工具调用统计

#### Scenario: 不得误用其它 Agent

- **WHEN** 执行 `evals.agent.*` 离线评测
- **THEN** runner **SHALL NOT** 调用 `CommonReactAgent`、`FaultOperationAgent` 或测试用例 `case_graph`

### Requirement: perf 数据集范围

`evals/agent/perf/datasets/deep_research/dataset.jsonl` 中的任务 SHALL 聚焦检索、代码智能、结构化报告合成及（可选）安全拒绝类场景。数据集 **SHALL NOT** 包含依赖邮件客户端、日历、即时通讯、电商生活流程的评测项。每条任务 SHOULD 在 `provenance` 中标注来源。

BrowseComp、WildClawBench **SHALL** 使用各自上游官方数据集与评分流程，**SHALL NOT** 复用 perf `dataset.jsonl`。

#### Scenario: 检索类任务纳入 perf

- **WHEN** 任务要求 Agent 通过联网检索公开信息并写入工作区文件
- **THEN** 该任务 MAY 出现在 perf 数据集中

#### Scenario: 邮件类任务不得纳入 perf

- **WHEN** 任务规格要求发送邮件、读取收件箱或操作日历邀请
- **THEN** 该任务 **SHALL NOT** 出现在 perf 数据集中

### Requirement: 工作区隔离与种子资产

每条评测任务 SHALL 在运行前获得独立工作区目录（位于 `.data/users/` 下 eval 专用路径或 runner 约定的临时 session 路径）。若条目声明 `workspace_seed`，runner SHALL 将种子目录（如 `perf/datasets/deep_research/workspaces/`）复制到该工作区后再启动 Agent。单次 run 内不同 `item_id` **SHALL NOT** 共享可写工作区。

#### Scenario: 带种子代码的任务

- **WHEN** perf 条目含 `workspace_seed` 指向 `workspaces/dr_code_sam3_debug/`
- **THEN** runner SHALL 在 Agent 启动前将种子文件复制到本次任务工作区

### Requirement: 混合评分在 Agent 结束后执行

perf 子模块 SHALL 在 Agent 运行结束后对工作区产物与最终文本执行混合评分：确定性规则 criteria → 工作区副作用检查 →（若存在）`semantic_rubric` 的 LLM Judge。评分逻辑 **SHALL NOT** 在 Agent 执行期间向 Agent 暴露 `ground_truth`。

BrowseComp / WildClawBench **SHALL** 使用各自官方 grader，**SHALL NOT** 强制复用 perf 混合评分器。

#### Scenario: 规则项判定文件产物

- **WHEN** perf `ground_truth.criteria` 含 `type=file_exists` 且 Agent 已在约定路径创建文件
- **THEN** 评分结果 SHALL 将该 criterion 记为 `passed=true`，且不调用 LLM

#### Scenario: 语义 rubric 调用真实 LLM

- **WHEN** perf 条目含非空 `semantic_rubric` 且规则项已执行完毕
- **THEN** 系统 SHALL 使用 `get_llm()` 进行 Judge，**SHALL NOT** 提供 mock 开关

### Requirement: 评测结果持久化与汇总

每次 `--tag` 运行 SHALL 在各子模块的 `results/<tag>/` 下写入单条结果与 `summary.json` / `summary.md`（路径以 `backend/evals/README.md` 为准）。

#### Scenario: 单条任务结果可查

- **WHEN** 某条 `item_id` 评测完成
- **THEN** 系统 SHALL 在对应 `results/<tag>/` 下写入可解析的结果文件

#### Scenario: 与历史 tag 对比

- **WHEN** CLI 传入 `--compare-to` 指向历史 baseline tag
- **THEN** 系统 SHALL 在汇总报告中输出分数差值（若子模块实现支持）

### Requirement: CLI 与 CI 策略

各 benchmark 子模块 CLI **SHALL** 至少支持 `--tag`（必填）及该模块文档所列过滤参数（如 `--item-id`、`--limit`）。全量 Agent benchmark 评测 **SHALL NOT** 作为 CI 必需步骤。

#### Scenario: perf 调试单题

- **WHEN** 开发者执行 `uv run python -m evals.agent.perf --tag debug --item-id dr_code_sam3_debug`
- **THEN** 系统 SHALL 仅运行该条目一次并输出结果
