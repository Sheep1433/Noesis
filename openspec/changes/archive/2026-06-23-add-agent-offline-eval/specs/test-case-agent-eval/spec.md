## ADDED Requirements

### Requirement: 测试用例评测入口为 evals.case 子模块

测试用例 Agent 离线评测 SHALL 通过 `uv run python -m evals.case` 启动。`uv run python -m evals` **SHALL NOT** 直接执行 promptfoo 评测，仅 SHALL 输出各场景子模块说明。

#### Scenario: 根包不跑分

- **WHEN** 开发者运行 `uv run python -m evals`
- **THEN** 系统 SHALL 打印 `evals.case` / `evals.agent` / `evals.compression` 用法提示，**SHALL NOT** 调用 promptfoo

#### Scenario: 测试用例评测入口

- **WHEN** 开发者运行 `uv run python -m evals.case --tag baseline`
- **THEN** 系统 SHALL 执行测试用例 promptfoo 评测流水线

### Requirement: 测试用例评测代码位于 evals/case

测试用例评测实现（`dataset.py`、`runner.py`、`scoring.py`、`promptfoo/`、`datasets/test_case/`）SHALL 位于 `backend/evals/case/`。数据集默认路径 SHALL 为 `backend/evals/case/datasets/test_case/dataset.jsonl`。

#### Scenario: 数据集路径

- **WHEN** runner 加载默认数据集且未指定 `--dataset`
- **THEN** 系统 SHALL 读取 `evals/case/datasets/test_case/dataset.jsonl`

### Requirement: 测试用例评测无质量门与 mock Judge

测试用例 promptfoo 评测 SHALL **NOT** 依赖 `eval_targets.json`、`--mock-judge` 或 `NOESIS_EVAL_MOCK_JUDGE`。`assert_coverage` 与 `assert_rag` SHALL 汇报指标分数；`assert_l0` SHALL 对结构/schema 失败返回 `pass=false`。coverage Judge SHALL 调用 `get_llm()`。

#### Scenario: coverage 无阈值门禁

- **WHEN** promptfoo 计算 `point_coverage_recall=0.42`
- **THEN** 断言 SHALL 记录该分数，**SHALL NOT** 因仓库级阈值自动失败

#### Scenario: coverage 使用真实 LLM

- **WHEN** 运行 `uv run python -m evals.case --tag manual` 且 scope 含 testpoints
- **THEN** `score_coverage` SHALL 调用 `get_llm()`，**SHALL NOT** 使用名称匹配 mock
