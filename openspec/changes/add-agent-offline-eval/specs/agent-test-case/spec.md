## MODIFIED Requirements

### Requirement: 金标准数据集

系统 SHALL 在 `backend/evals/case/datasets/test_case/dataset.jsonl` 维护数据集。每条 item SHALL 含：`id`、`scenario_description`、`document_path`、`ground_truth.golden_test_points`。评 RAG 的 item MAY 含 `ground_truth.expected_rag`（channel：`current_requirement`、`historical_requirements`、`historical_test_cases`）；未标注时 rag scorer 跳过该项。

#### Scenario: 数据集规模不强制扩展

- **WHEN** 审查离线评测数据集
- **THEN** SHALL 以仓库现有条目为准验收，**SHALL NOT** 将「至少 20 条」作为通过条件

### Requirement: 离线 runner

系统 SHALL 通过 `uv run python -m evals.case` 启动离线评测，并支持 `--scope testpoints|cases|full`，直调 `case_graph` 节点，不经 SSE。`cases`/`full` SHALL 采纳全部 `point_name`。

#### Scenario: testpoints scope 仅评阶段 A

- **WHEN** runner 以 `--scope testpoints` 执行
- **THEN** SHALL 仅调用 `generate_scenes_testpoints` 相关路径，**SHALL NOT** 依赖 resume 或用户交互
