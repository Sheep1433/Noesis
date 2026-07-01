# agent-offline-eval Specification

## Purpose

本能力规定 Noesis **Agent 端到端离线评测**（`backend/evals/agent/`）的验收标准：按 benchmark 分子模块（BrowseComp、Terminal-Bench/Harbor）、共用 `SuperAgent` / `DeepResearchAgent` 执行（Harbor 线除外，见各子模块 README）、工作区隔离、评分与结果持久化，以及与 `evals.case`、`evals.compression` 的目录隔离。

## Requirements

### Requirement: Agent 离线评测与测试用例评测目录隔离

Agent 评测 **SHALL NOT** 与 `evals/case/` 或 `evals/compression/` 共用 runner 或结果目录。

根包 `uv run python -m evals.agent` **SHALL** 打印 benchmark 子模块用法说明；实际跑分 **SHALL** 使用下列子模块之一：

| 子模块 | 命令示例 | 用途 |
|--------|----------|------|
| `evals.agent.browsecomp` | `uv run python -m evals.agent.browsecomp --tag bc-smoke` | BrowseComp 官方流程 |
| `evals/agent/harbor/run.sh` | `./evals/agent/harbor/run.sh --n-tasks 1 --job-name smoke` | Terminal-Bench（Harbor + Claude Code） |

测试用例评测入口 SHALL 为 `uv run python -m evals.case`（见 `test-case-agent-eval`）。

#### Scenario: 根 agent 包不直接跑分

- **WHEN** 开发者运行 `uv run python -m evals.agent` 且无子模块参数
- **THEN** 系统 SHALL 打印上述子模块说明，**SHALL NOT** 自动加载数据集并评分

#### Scenario: Agent 与测试用例评测独立执行

- **WHEN** 开发者运行 `uv run python -m evals.agent.browsecomp --tag bc-smoke`
- **THEN** 系统 SHALL 仅加载 `evals/agent/browsecomp/` 相关资产，**SHALL NOT** 读取 `evals/case/testpoints/`

#### Scenario: 根 evals 包不替代场景入口

- **WHEN** 开发者运行 `uv run python -m evals`
- **THEN** 系统 **SHALL NOT** 启动测试用例或 Agent 评测；开发者 SHALL 改用具体子模块

### Requirement: 评测执行 SuperAgent / DeepResearchAgent

Agent 离线 runner（各 benchmark 子模块经 `evals/agent/_agent.py` 或等价物）SHALL 调用 `SuperAgent` 或 `DeepResearchAgent`（与线上 Agent 工具栈一致）。**SHALL NOT** 支持切换为其它 qa_type 或 Agent 类（Harbor 线使用 Claude Code，除外）。

#### Scenario: 深度研究任务成功执行

- **WHEN** 数据集条目 `query` 非空
- **THEN** runner SHALL 在隔离工作区内调用 Agent 完成任务，并记录 `completed`、`latency_ms` 与工具调用统计

#### Scenario: 不得误用其它 Agent

- **WHEN** 执行 `evals.agent.*` 离线评测
- **THEN** runner **SHALL NOT** 调用 `CommonReactAgent`、`FaultOperationAgent` 或测试用例 `case_graph`

### Requirement: 官方 benchmark 数据集与评分

BrowseComp **SHALL** 使用上游官方 CSV 与 `BrowseCompEval` 评分流程。**SHALL NOT** 维护自研 `dataset.jsonl` 或自研混合 Judge 作为 Agent 离线评测主线。

#### Scenario: BrowseComp 使用官方 accuracy

- **WHEN** 开发者运行 `uv run python -m evals.agent.browsecomp --tag bc1`
- **THEN** 系统 SHALL 输出与 `BrowseCompEval` 一致的 **accuracy** 指标

### Requirement: 工作区隔离

每条 BrowseComp 评测任务 SHALL 在运行前获得独立工作区目录（位于 `.data/users/` 下 eval 专用路径或 runner 约定的临时 session 路径）。单次 run 内不同任务 **SHALL NOT** 共享可写工作区。

#### Scenario: BrowseComp session 隔离

- **WHEN** 连续执行两条 BrowseComp 样本
- **THEN** 系统 SHALL 为每条使用不同的 `session_id` 工作区

### Requirement: 评测结果持久化与汇总

每次 `--tag` 运行 SHALL 在各子模块的 `results/<tag>/` 下写入单条结果与 `summary.json`（路径以 `backend/evals/README.md` 为准）。

#### Scenario: 单条任务结果可查

- **WHEN** 某次 benchmark run 完成
- **THEN** 系统 SHALL 在对应 `results/<tag>/` 下写入可解析的结果文件

### Requirement: CLI 与 CI 策略

各 benchmark 子模块 CLI **SHALL** 至少支持 `--tag`（必填）及该模块文档所列过滤参数（如 BrowseComp `--num-examples`）。全量 Agent benchmark 评测 **SHALL NOT** 作为 CI 必需步骤。

#### Scenario: BrowseComp 子集调试

- **WHEN** 开发者执行 `uv run python -m evals.agent.browsecomp --tag debug --num-examples 5`
- **THEN** 系统 SHALL 仅评测 5 条样本并输出 summary
