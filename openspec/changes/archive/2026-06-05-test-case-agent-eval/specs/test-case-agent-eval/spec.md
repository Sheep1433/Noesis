## Purpose

本能力规定测试用例生成 Agent 的离线评测：**L0**、**coverage**（`point_coverage_recall`）、**rag**（`rag_hit_at_3`）。runner 支持 `--scope testpoints|cases|full`。

## Requirements

### Requirement: 金标准数据集

系统 SHALL 在 `backend/evals/datasets/test_case/dataset.jsonl` 维护数据集。每条 item SHALL 含：`id`、`scenario_description`、`document_path`、`ground_truth.golden_test_points`。评 RAG 的 item SHALL 含 `ground_truth.expected_rag`（channel：`current_requirement`、`historical_requirements`、`historical_test_cases`）。

条数不足 20 时，`aggregate.json` SHALL 含 `dataset_size_warning: true`。

### Requirement: 离线 runner

系统 SHALL 支持 `--scope testpoints|cases|full`，直调 `case_graph` 节点，不经 SSE。`cases`/`full` SHALL 采纳全部 `point_name`。

### Requirement: L0 结构门禁

系统 SHALL 校验 `scenes_testpoints` / `test_cases` 字段完整性且无 `error`。

### Requirement: coverage（测试点覆盖准确率）

系统 SHALL 用 LLM Judge 计算 `point_coverage_recall` = 已覆盖金标准数 / |golden_test_points|。pytest SHALL 使用 mock Judge。

### Requirement: rag（RAG Hit@3）

系统 SHALL 按 `scene_name` 对账 `retrieval_trace` 与 `expected_rag`，Top-3 `hit_ids` 交集非空为 hit。无 `expected_rag` 时 skipped；无 trace 时 `rag_eval_incomplete`。

### Requirement: 报告

`aggregate.json` SHALL 含 `l0_pass_rate`、`coverage.point_coverage_recall_mean`、`rag.rag_hit_at_3_mean`（若有）、`dataset_size_warning`。支持 `--baseline` delta。

### Requirement: CI

`docs/test/test_tdd_design.md` 登记三项指标；默认 pytest 不调 DashScope。
